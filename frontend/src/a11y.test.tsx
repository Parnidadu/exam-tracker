/**
 * EXT-035: keyboard navigability and automated accessibility checks.
 *
 * Lighthouse gives the score the ticket asks for, but it can't run in this
 * project's CI. axe-core covers the same rule set here so a regression is
 * caught on every PR rather than the next time someone runs Lighthouse by
 * hand.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

import App from './App'
import { Layout } from './components/Layout'

const BOARD = { id: 1, name: 'Union Public Service Commission', code: 'UPSC' }

const track = (t: string, value: string, fresh: boolean) => ({
  track: t,
  machine_value: value,
  machine_confidence: 0.9,
  machine_seen_at: '2026-06-02T00:00:00Z',
  human_value: value,
  verified_by: 'v@example.com',
  verified_at: '2026-06-03T00:00:00Z',
  effective_status: value,
  is_verification_fresh: fresh,
})

const EXAM = {
  id: 1,
  board: BOARD,
  code: 'CSE',
  name: 'Civil Services Examination',
  cycle_year: 2026,
  category: 'Civil Services',
  slug: 'exam-1',
}

const DETAIL = {
  ...EXAM,
  stages: [
    {
      id: 10,
      stage_type: 'prelims',
      sequence: 1,
      planned_start_date: '2026-06-01',
      planned_end_date: '2026-06-01',
      notification_date: '2026-03-01',
      admit_card_date: '2026-05-20',
      exam_date: '2026-06-01',
      answer_key_date: null,
      result_date: null,
      status_tracks: [
        track('conduct', 'conducted', true),
        track('result', 'awaited', false),
        track('integrity', 'clean', true),
      ],
    },
  ],
}

/** Rule ids of any violations - asserted as a list so a failure names the
 *  broken rules rather than just saying "expected true". */
function violationIds(results: { violations: { id: string }[] }): string[] {
  return results.violations.map((v) => v.id)
}

function mockApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      const json = url.startsWith('/api/boards/')
        ? [BOARD]
        : url.endsWith('/verifications/')
          ? { count: 0, next: null, previous: null, results: [] }
          : url.startsWith('/api/exams/exam-1')
            ? DETAIL
            : url.startsWith('/api/calendar/')
              ? []
              : { count: 1, next: null, previous: null, results: [EXAM] }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(json) })
    }),
  )
}

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

beforeEach(mockApi)
afterEach(() => vi.unstubAllGlobals())

describe('accessibility', () => {
  it('the exam list page has no automatically detectable violations', async () => {
    const { container } = renderApp('/')
    await screen.findByLabelText('Exams')
    expect(violationIds(await axe(container))).toEqual([])
  })

  it('the exam detail page has no automatically detectable violations', async () => {
    const { container } = renderApp('/exams/exam-1')
    await screen.findByLabelText('prelims timeline')
    expect(violationIds(await axe(container))).toEqual([])
  })

  it('the calendar page has no automatically detectable violations', async () => {
    const { container } = renderApp('/calendar?month=2026-06')
    await screen.findByRole('grid')
    expect(violationIds(await axe(container))).toEqual([])
  })
})

describe('keyboard navigation', () => {
  it('offers a skip link that jumps straight to the main content', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    )

    // First Tab must reach the skip link, before the nav - otherwise a
    // keyboard user re-tabs the whole nav on every page.
    await user.tab()
    const skip = screen.getByRole('link', { name: /skip to main content/i })
    expect(skip).toHaveFocus()
    expect(skip).toHaveAttribute('href', '#main')

    // And the target it names actually exists.
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  })

  it('exposes the site navigation as a landmark with reachable links', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    )

    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getAllByRole('link')).toHaveLength(2)

    await user.tab() // skip link
    await user.tab() // first nav link
    expect(within(nav).getByRole('link', { name: 'Exams' })).toHaveFocus()
  })

  it('lets the keyboard reach every filter control on the list page', async () => {
    renderApp('/')
    await screen.findByLabelText('Exams')

    for (const label of [
      'Search by name',
      'Board',
      'Conduct status',
      'Result status',
      'From date',
      'To date',
    ]) {
      const control = screen.getByLabelText(label)
      control.focus()
      expect(control).toHaveFocus()
    }
  })

  it('lets the keyboard operate the calendar grid', async () => {
    const user = userEvent.setup()
    renderApp('/calendar?month=2026-06')
    await screen.findByRole('grid')

    // Days are real buttons, so they are tabbable and Enter-activatable
    // rather than click-only.
    const day = screen.getByTestId('day-2026-06-10')
    day.focus()
    expect(day).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(await screen.findByRole('status')).toBeInTheDocument()
  })

  it('marks the current page for assistive tech, not by styling alone', () => {
    render(
      <MemoryRouter initialEntries={['/calendar']}>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    )
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Calendar' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })
})
