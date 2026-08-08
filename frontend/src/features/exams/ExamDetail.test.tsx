import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ExamDetail as ExamDetailData, VerificationRecord } from '../../api/types'
import { ExamDetail } from './ExamDetail'

const EXAM: ExamDetailData = {
  id: 1,
  board: { id: 1, name: 'Union Public Service Commission', code: 'UPSC' },
  code: 'CSE',
  name: 'Civil Services Examination',
  cycle_year: 2026,
  category: 'Civil Services',
  slug: 'upsc-cse-2026',
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
        {
          track: 'conduct',
          machine_value: 'conducted',
          machine_confidence: 0.9,
          machine_seen_at: '2026-06-02T00:00:00Z',
          human_value: 'conducted',
          verified_by: 'v@example.com',
          verified_at: '2026-06-03T00:00:00Z',
          effective_status: 'conducted',
          is_verification_fresh: true,
        },
        {
          track: 'result',
          machine_value: '',
          machine_confidence: null,
          machine_seen_at: null,
          human_value: 'awaited',
          verified_by: 'v@example.com',
          verified_at: '2026-06-04T00:00:00Z',
          effective_status: 'awaited',
          // Deliberately stale, so the page exercises both badge states.
          is_verification_fresh: false,
        },
      ],
    },
  ],
}

function record(over: Partial<VerificationRecord> & { id: number }): VerificationRecord {
  return {
    exam_stage_id: 10,
    stage_type: 'prelims',
    sequence: 1,
    track: 'conduct',
    value: 'conducted',
    evidence_url: 'https://upsc.gov.in/n/1',
    note: '',
    actor: 'v@example.com',
    timestamp: '2026-06-03T10:00:00Z',
    ...over,
  }
}

function mockApi(records: VerificationRecord[], exam: ExamDetailData = EXAM) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      const body = url.endsWith('/verifications/')
        ? { count: records.length, next: null, previous: null, results: records }
        : exam
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    }),
  )
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/exams/upsc-cse-2026']}>
      <Routes>
        <Route path="/exams/:slug" element={<ExamDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('ExamDetail', () => {
  it('shows the current value for every status track', async () => {
    mockApi([])
    renderDetail()

    expect(await screen.findByTestId('current-10-conduct')).toHaveTextContent('conducted')
    expect(screen.getByTestId('current-10-result')).toHaveTextContent('awaited')
  })

  it('renders a stage timeline with all five milestones', async () => {
    mockApi([])
    renderDetail()

    const timeline = await screen.findByTestId('timeline-10')
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(5)
    expect(within(timeline).getByText('Notification')).toBeInTheDocument()
    expect(within(timeline).getByText('Result')).toBeInTheDocument()
    // Milestones with no date still appear, marked as unannounced.
    expect(screen.getByTestId('milestone-10-result_date')).toHaveAttribute(
      'data-state',
      'unannounced',
    )
  })

  it('renders each track as a status badge carrying its own freshness', async () => {
    mockApi([])
    renderDetail()

    const conduct = await screen.findByTestId('current-10-conduct')
    const result = screen.getByTestId('current-10-result')

    expect(within(conduct).getByTestId('status-badge')).toHaveAttribute(
      'data-freshness',
      'fresh',
    )
    expect(within(result).getByTestId('status-badge')).toHaveAttribute(
      'data-freshness',
      'stale',
    )
  })

  it('lists past verifications for a track in reverse-chronological order', async () => {
    mockApi([
      record({ id: 3, value: 'conducted', timestamp: '2026-06-03T10:00:00Z' }),
      record({ id: 2, value: 'postponed', timestamp: '2026-06-02T10:00:00Z' }),
      record({ id: 1, value: 'scheduled', timestamp: '2026-06-01T10:00:00Z' }),
    ])
    renderDetail()

    const section = await screen.findByLabelText('prelims conduct track')
    const entries = within(section).getAllByRole('listitem')

    expect(entries).toHaveLength(3)
    expect(entries[0]).toHaveTextContent('conducted')
    expect(entries[1]).toHaveTextContent('postponed')
    expect(entries[2]).toHaveTextContent('scheduled')
  })

  it('files each verification under its own track, not all of them', async () => {
    mockApi([
      record({ id: 1, track: 'conduct', value: 'conducted' }),
      record({ id: 2, track: 'result', value: 'declared' }),
    ])
    renderDetail()

    const conduct = await screen.findByLabelText('prelims conduct track')
    const result = screen.getByLabelText('prelims result track')

    // Scoped to the history entries: "conducted" also appears above as the
    // track's current value, so a section-wide text query would be ambiguous.
    const conductEntries = within(conduct).getAllByRole('listitem')
    const resultEntries = within(result).getAllByRole('listitem')

    expect(conductEntries).toHaveLength(1)
    expect(conductEntries[0]).toHaveTextContent('conducted')
    expect(resultEntries).toHaveLength(1)
    expect(resultEntries[0]).toHaveTextContent('declared')
  })

  it('says so when a track has no verifications yet', async () => {
    mockApi([record({ id: 1, track: 'conduct' })])
    renderDetail()

    const result = await screen.findByLabelText('prelims result track')
    expect(within(result).getByText('No verifications recorded yet.')).toBeInTheDocument()
  })

  it('shows the actor when the API returns one', async () => {
    mockApi([record({ id: 1, actor: 'someone@example.com' })])
    renderDetail()

    const conduct = await screen.findByLabelText('prelims conduct track')
    expect(within(conduct).getByText(/someone@example.com/)).toBeInTheDocument()
  })

  it('degrades gracefully when the actor is withheld for anonymous visitors', async () => {
    mockApi([record({ id: 1, actor: null })])
    renderDetail()

    const conduct = await screen.findByLabelText('prelims conduct track')
    expect(within(conduct).getByText(/sign in to see who/)).toBeInTheDocument()
  })

  it('links to the evidence URL', async () => {
    mockApi([record({ id: 1, evidence_url: 'https://upsc.gov.in/n/42' })])
    renderDetail()

    const conduct = await screen.findByLabelText('prelims conduct track')
    expect(within(conduct).getByRole('link', { name: 'evidence' })).toHaveAttribute(
      'href',
      'https://upsc.gov.in/n/42',
    )
  })

  it('reports a load failure', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, status: 500 })))
    renderDetail()

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this exam.')
  })
})
