import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Board, ExamSummary } from '../../api/types'
import { ExamList } from './ExamList'

const BOARDS: Board[] = [
  { id: 1, name: 'Union Public Service Commission', code: 'UPSC' },
  { id: 2, name: 'Staff Selection Commission', code: 'SSC' },
]

function exam(id: number, name: string): ExamSummary {
  return {
    id,
    board: BOARDS[0],
    code: `E${id}`,
    name,
    cycle_year: 2026,
    category: 'Civil Services',
    slug: `exam-${id}`,
  }
}

/** Records every /api/exams/ URL the component requested. */
function mockApi(results: ExamSummary[] = [exam(1, 'Civil Services Examination')]) {
  const examCalls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url.startsWith('/api/boards/')) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(BOARDS) })
      }
      examCalls.push(url)
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ count: results.length, next: null, previous: null, results }),
      })
    }),
  )
  return examCalls
}

function LocationSpy() {
  const location = useLocation()
  return <span data-testid="url">{location.search}</span>
}

function renderList(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/"
          element={
            <>
              <ExamList />
              <LocationSpy />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

function lastQuery(calls: string[]): URLSearchParams {
  return new URLSearchParams(calls[calls.length - 1].split('?')[1] ?? '')
}

afterEach(() => vi.unstubAllGlobals())

describe('ExamList', () => {
  it('lists exams returned by the API', async () => {
    mockApi([exam(1, 'Civil Services Examination'), exam(2, 'Combined Defence Services')])
    renderList()

    const list = await screen.findByLabelText('Exams')
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByTestId('result-count')).toHaveTextContent('2 exams')
  })

  it('filters by board and reflects it in the URL', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderList()
    await screen.findByLabelText('Exams')

    await user.selectOptions(screen.getByLabelText('Board'), 'SSC')

    await waitFor(() => expect(lastQuery(calls).get('board')).toBe('SSC'))
    expect(screen.getByTestId('url')).toHaveTextContent('board=SSC')
  })

  it('filters by conduct status and reflects it in the URL', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderList()
    await screen.findByLabelText('Exams')

    await user.selectOptions(screen.getByLabelText('Conduct status'), 'conducted')

    await waitFor(() => expect(lastQuery(calls).get('conduct_status')).toBe('conducted'))
    expect(screen.getByTestId('url')).toHaveTextContent('conduct_status=conducted')
  })

  it('filters by result status and reflects it in the URL', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderList()
    await screen.findByLabelText('Exams')

    await user.selectOptions(screen.getByLabelText('Result status'), 'awaited')

    await waitFor(() => expect(lastQuery(calls).get('result_status')).toBe('awaited'))
    expect(screen.getByTestId('url')).toHaveTextContent('result_status=awaited')
  })

  it('filters by date range and reflects it in the URL', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderList()
    await screen.findByLabelText('Exams')

    await user.type(screen.getByLabelText('From date'), '2026-06-01')
    await user.type(screen.getByLabelText('To date'), '2026-06-30')

    await waitFor(() => {
      const query = lastQuery(calls)
      expect(query.get('start_date')).toBe('2026-06-01')
      expect(query.get('end_date')).toBe('2026-06-30')
    })
    expect(screen.getByTestId('url')).toHaveTextContent('start_date=2026-06-01')
  })

  it('searches by exam name and reflects it in the URL', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderList()
    await screen.findByLabelText('Exams')

    await user.type(screen.getByLabelText('Search by name'), 'civil')

    await waitFor(() => expect(lastQuery(calls).get('search')).toBe('civil'))
    expect(screen.getByTestId('url')).toHaveTextContent('search=civil')
  })

  it('restores filter state from the URL on load', async () => {
    const calls = mockApi()
    renderList('/?search=civil&board=SSC&conduct_status=conducted&result_status=awaited')
    await screen.findByLabelText('Exams')

    // The controls reflect the URL...
    expect(screen.getByLabelText('Search by name')).toHaveValue('civil')
    expect(screen.getByLabelText('Board')).toHaveValue('SSC')
    expect(screen.getByLabelText('Conduct status')).toHaveValue('conducted')
    expect(screen.getByLabelText('Result status')).toHaveValue('awaited')

    // ...and so does the request, so a shared link reproduces the results.
    const query = lastQuery(calls)
    expect(query.get('search')).toBe('civil')
    expect(query.get('board')).toBe('SSC')
    expect(query.get('conduct_status')).toBe('conducted')
    expect(query.get('result_status')).toBe('awaited')
  })

  it('combines several filters into one request', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderList('/?board=UPSC')
    await screen.findByLabelText('Exams')

    await user.selectOptions(screen.getByLabelText('Conduct status'), 'conducted')

    await waitFor(() => {
      const query = lastQuery(calls)
      expect(query.get('board')).toBe('UPSC')
      expect(query.get('conduct_status')).toBe('conducted')
    })
  })

  it('does not send empty filters as blank query parameters', async () => {
    const calls = mockApi()
    renderList()
    await screen.findByLabelText('Exams')

    expect(calls[0]).toBe('/api/exams/')
  })

  it('clears every filter at once', async () => {
    mockApi()
    const user = userEvent.setup()
    renderList('/?search=civil&board=SSC')
    await screen.findByLabelText('Exams')

    await user.click(screen.getByRole('button', { name: /clear all filters/i }))

    await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent(''))
    expect(screen.getByLabelText('Search by name')).toHaveValue('')
    expect(screen.getByLabelText('Board')).toHaveValue('')
  })

  it('says so when nothing matches', async () => {
    mockApi([])
    renderList()

    expect(await screen.findByRole('status')).toHaveTextContent('No exams match these filters.')
  })

  it('reports a load failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        url.startsWith('/api/boards/')
          ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
          : Promise.resolve({ ok: false, status: 500 }),
      ),
    )
    renderList()

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load exams.')
  })
})
