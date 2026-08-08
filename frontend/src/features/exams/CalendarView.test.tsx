import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CalendarEntry } from '../../api/types'
import { CalendarView } from './CalendarView'

function entry(over: Partial<CalendarEntry> = {}): CalendarEntry {
  return {
    date: '2026-06-10',
    milestone: 'exam',
    milestone_label: 'Exam date',
    exam_slug: 'upsc-cse-2026',
    exam_name: 'Civil Services Examination',
    board_code: 'UPSC',
    stage_type: 'prelims',
    ...over,
  }
}

function mockApi(entries: CalendarEntry[]) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      calls.push(url)
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(entries) })
    }),
  )
  return calls
}

function LocationSpy() {
  return <span data-testid="url">{useLocation().search}</span>
}

function renderCalendar(initial = '/calendar?month=2026-06') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route
          path="/calendar"
          element={
            <>
              <CalendarView />
              <LocationSpy />
            </>
          }
        />
        <Route path="/exams/:slug" element={<p>exam detail page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('CalendarView', () => {
  it('renders a month grid with a cell for every day of the month', async () => {
    mockApi([])
    renderCalendar()

    // June 2026 has 30 days.
    await screen.findByTestId('day-2026-06-01')
    expect(screen.getByTestId('day-2026-06-30')).toBeInTheDocument()
    expect(screen.queryByTestId('day-2026-06-31')).not.toBeInTheDocument()
  })

  it('labels the grid with seven weekday columns', async () => {
    mockApi([])
    renderCalendar()

    const grid = await screen.findByRole('grid')
    expect(within(grid).getAllByRole('columnheader')).toHaveLength(7)
  })

  it('shows the month it is displaying', async () => {
    mockApi([])
    renderCalendar()
    expect(await screen.findByTestId('month-label')).toHaveTextContent('June 2026')
  })

  it('marks days that have events with a count', async () => {
    mockApi([
      entry({ date: '2026-06-10' }),
      entry({ date: '2026-06-10', milestone: 'admit_card' }),
      entry({ date: '2026-06-12' }),
    ])
    renderCalendar()

    await waitFor(() =>
      expect(screen.getByTestId('day-2026-06-10')).toHaveAttribute('data-count', '2'),
    )
    expect(screen.getByTestId('day-2026-06-12')).toHaveAttribute('data-count', '1')
    expect(screen.getByTestId('day-2026-06-11')).toHaveAttribute('data-count', '0')
  })

  it('opens that day’s exams when a date is clicked', async () => {
    mockApi([
      entry({ date: '2026-06-10', exam_name: 'Civil Services Examination' }),
      entry({ date: '2026-06-12', exam_name: 'Combined Defence Services' }),
    ])
    const user = userEvent.setup()
    renderCalendar()

    await user.click(await screen.findByTestId('day-2026-06-10'))

    const list = await screen.findByLabelText('Exams on 2026-06-10')
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText('Civil Services Examination')).toBeInTheDocument()
    expect(within(list).getByText(/Exam date/)).toBeInTheDocument()
  })

  it('reflects the selected day in the URL so it can be shared', async () => {
    mockApi([entry({ date: '2026-06-10' })])
    const user = userEvent.setup()
    renderCalendar()

    await user.click(await screen.findByTestId('day-2026-06-10'))

    expect(screen.getByTestId('url')).toHaveTextContent('date=2026-06-10')
  })

  it('restores the selected day from the URL', async () => {
    mockApi([entry({ date: '2026-06-10' })])
    renderCalendar('/calendar?month=2026-06&date=2026-06-10')

    expect(await screen.findByLabelText('Exams on 2026-06-10')).toBeInTheDocument()
  })

  it('says so when the selected day has nothing scheduled', async () => {
    mockApi([entry({ date: '2026-06-10' })])
    const user = userEvent.setup()
    renderCalendar()

    await user.click(await screen.findByTestId('day-2026-06-11'))

    expect(await screen.findByRole('status')).toHaveTextContent('Nothing scheduled')
  })

  it('links each entry through to its exam', async () => {
    mockApi([entry({ date: '2026-06-10' })])
    const user = userEvent.setup()
    renderCalendar()

    await user.click(await screen.findByTestId('day-2026-06-10'))
    const link = within(await screen.findByLabelText('Exams on 2026-06-10')).getByRole('link')

    expect(link).toHaveAttribute('href', '/exams/upsc-cse-2026')
  })

  it('moves between months and refetches', async () => {
    const calls = mockApi([])
    const user = userEvent.setup()
    renderCalendar()
    await screen.findByTestId('month-label')

    await user.click(screen.getByRole('button', { name: 'Next month' }))
    await waitFor(() => expect(screen.getByTestId('month-label')).toHaveTextContent('July 2026'))
    expect(calls.some((url) => url.includes('month=2026-07'))).toBe(true)

    await user.click(screen.getByRole('button', { name: 'Previous month' }))
    await waitFor(() => expect(screen.getByTestId('month-label')).toHaveTextContent('June 2026'))
  })

  it('rolls over the year at December and January', async () => {
    mockApi([])
    const user = userEvent.setup()
    renderCalendar('/calendar?month=2026-12')
    await screen.findByTestId('month-label')

    await user.click(screen.getByRole('button', { name: 'Next month' }))
    await waitFor(() =>
      expect(screen.getByTestId('month-label')).toHaveTextContent('January 2027'),
    )
  })

  it('clears a selected day when the month changes', async () => {
    mockApi([entry({ date: '2026-06-10' })])
    const user = userEvent.setup()
    renderCalendar('/calendar?month=2026-06&date=2026-06-10')
    await screen.findByLabelText('Exams on 2026-06-10')

    await user.click(screen.getByRole('button', { name: 'Next month' }))

    // A day from the old month would otherwise stay "selected" while being
    // absent from the grid.
    await waitFor(() => expect(screen.getByTestId('url')).not.toHaveTextContent('date='))
  })

  it('lays out February 2027 correctly, starting on the right weekday', async () => {
    mockApi([])
    renderCalendar('/calendar?month=2027-02')

    await screen.findByTestId('day-2027-02-01')
    expect(screen.getByTestId('day-2027-02-28')).toBeInTheDocument()
    expect(screen.queryByTestId('day-2027-02-29')).not.toBeInTheDocument()
  })

  it('includes 29 February in a leap year', async () => {
    mockApi([])
    renderCalendar('/calendar?month=2028-02')

    expect(await screen.findByTestId('day-2028-02-29')).toBeInTheDocument()
  })

  it('reports a load failure', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, status: 500 })))
    renderCalendar()

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the calendar.')
  })
})
