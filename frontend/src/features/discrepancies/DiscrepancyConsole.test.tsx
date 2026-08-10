import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '../../api/client'
import type { Discrepancy, ExamDetail, ExamSummary, Paginated } from '../../api/types'
import { DiscrepancyConsole } from './DiscrepancyConsole'

const EXAM: ExamSummary = {
  id: 1,
  board: { id: 1, name: 'Union Public Service Commission', code: 'UPSC' },
  code: 'CSE',
  name: 'Civil Services Examination',
  cycle_year: 2026,
  category: 'civil-services',
  slug: 'upsc-cse-2026',
}

const EXAM_DETAIL = {
  ...EXAM,
  stages: [
    {
      id: 7,
      stage_type: 'prelims',
      sequence: 1,
      planned_start_date: null,
      planned_end_date: null,
      notification_date: null,
      admit_card_date: null,
      exam_date: null,
      answer_key_date: null,
      result_date: null,
      status_tracks: [],
    },
  ],
} as unknown as ExamDetail

function discrepancy(overrides: Partial<Discrepancy> = {}): Discrepancy {
  return {
    id: 11,
    exam_stage: 7,
    exam_slug: 'upsc-cse-2026',
    exam_name: 'Civil Services Examination',
    board_code: 'UPSC',
    stage_type: 'prelims',
    discrepancy_type: 'postponement',
    severity: 'medium',
    status: 'reported',
    description: 'Postponed by two weeks.',
    evidence_url: 'https://www.upsc.gov.in/notice',
    evidence_note: '',
    occurred_on: null,
    reported_by: 'verifier@example.gov.in',
    reported_at: '2026-01-15T10:00:00Z',
    resolved_by: null,
    resolved_at: null,
    resolution_note: '',
    is_open: true,
    available_transitions: ['confirmed', 'dismissed'],
    ...overrides,
  }
}

function page<T>(results: T[]): Paginated<T> {
  return { count: results.length, next: null, previous: null, results }
}

beforeEach(() => {
  vi.spyOn(client, 'fetchExams').mockResolvedValue(page([EXAM]))
  vi.spyOn(client, 'fetchExamDetail').mockResolvedValue(EXAM_DETAIL)
  vi.spyOn(client, 'fetchDiscrepancies').mockResolvedValue(page([discrepancy()]))
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function openForm() {
  const user = userEvent.setup()
  render(<DiscrepancyConsole />)
  await screen.findByText('Postponed by two weeks.')

  await user.selectOptions(screen.getByLabelText('Exam'), 'upsc-cse-2026')
  await waitFor(() => expect(screen.getByLabelText('Stage')).not.toBeDisabled())
  return user
}

describe('opening a discrepancy', () => {
  it('sends what the verifier filled in', async () => {
    const open = vi.spyOn(client, 'openDiscrepancy').mockResolvedValue(discrepancy())
    const user = await openForm()

    await user.selectOptions(screen.getByLabelText('Type'), 'paper_leak')
    await user.selectOptions(screen.getByLabelText('Severity'), 'critical')
    await user.type(screen.getByLabelText('What happened'), 'Paper circulated before the exam.')
    await user.type(
      screen.getByLabelText('Evidence URL (required)'),
      'https://www.upsc.gov.in/notice',
    )
    await user.click(screen.getByRole('button', { name: 'Open discrepancy' }))

    await waitFor(() => expect(open).toHaveBeenCalled())
    expect(open.mock.calls[0][0]).toMatchObject({
      exam_stage: 7,
      discrepancy_type: 'paper_leak',
      severity: 'critical',
      description: 'Paper circulated before the exam.',
      evidence_url: 'https://www.upsc.gov.in/notice',
    })
  })

  it('refuses to submit without an evidence URL', async () => {
    const open = vi.spyOn(client, 'openDiscrepancy')
    const user = await openForm()

    await user.type(screen.getByLabelText('What happened'), 'Something happened.')
    await user.click(screen.getByRole('button', { name: 'Open discrepancy' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/evidence url is required/i)
    expect(open).not.toHaveBeenCalled()
  })

  it('refuses an evidence URL that is not a URL', async () => {
    const open = vi.spyOn(client, 'openDiscrepancy')
    const user = await openForm()

    await user.type(screen.getByLabelText('What happened'), 'Something happened.')
    await user.type(screen.getByLabelText('Evidence URL (required)'), 'upsc.gov.in/notice')
    await user.click(screen.getByRole('button', { name: 'Open discrepancy' }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(open).not.toHaveBeenCalled()
  })

  it('says which detail is missing rather than failing vaguely', async () => {
    const user = await openForm()

    await user.type(
      screen.getByLabelText('Evidence URL (required)'),
      'https://www.upsc.gov.in/notice',
    )
    await user.click(screen.getByRole('button', { name: 'Open discrepancy' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/describe what happened/i)
  })

  it('does not offer stages until an exam is chosen', () => {
    render(<DiscrepancyConsole />)

    expect(screen.getByLabelText('Stage')).toBeDisabled()
  })
})

describe('updating and resolving', () => {
  it('offers only the moves the server says are possible', async () => {
    render(<DiscrepancyConsole />)
    const item = await screen.findByRole('listitem')

    expect(within(item).getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
    expect(within(item).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()
    expect(within(item).queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument()
  })

  it('confirms without demanding a reason', async () => {
    const move = vi
      .spyOn(client, 'transitionDiscrepancy')
      .mockResolvedValue(discrepancy({ status: 'confirmed' }))
    const user = userEvent.setup()
    render(<DiscrepancyConsole />)
    await screen.findByRole('listitem')

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(move).toHaveBeenCalledWith(11, { status: 'confirmed' }))
  })

  it('asks why before resolving, and sends the reason', async () => {
    vi.spyOn(client, 'fetchDiscrepancies').mockResolvedValue(
      page([discrepancy({ status: 'confirmed', available_transitions: ['dismissed', 'resolved'] })]),
    )
    const move = vi
      .spyOn(client, 'transitionDiscrepancy')
      .mockResolvedValue(discrepancy({ status: 'resolved' }))
    const user = userEvent.setup()
    render(<DiscrepancyConsole />)
    await screen.findByRole('listitem')

    await user.click(screen.getByRole('button', { name: 'Resolve' }))
    await user.type(screen.getByLabelText(/why\?/i), 'Re-conducted on 12 March.')
    // Two "Resolve" buttons now exist: the one that opened the reason
    // form, and the one that submits it. The submit is the later.
    await user.click(screen.getAllByRole('button', { name: 'Resolve' }).at(-1)!)

    await waitFor(() =>
      expect(move).toHaveBeenCalledWith(11, {
        status: 'resolved',
        resolution_note: 'Re-conducted on 12 March.',
      }),
    )
  })

  it('will not resolve with an empty reason', async () => {
    vi.spyOn(client, 'fetchDiscrepancies').mockResolvedValue(
      page([discrepancy({ status: 'confirmed', available_transitions: ['resolved'] })]),
    )
    const move = vi.spyOn(client, 'transitionDiscrepancy')
    const user = userEvent.setup()
    render(<DiscrepancyConsole />)
    await screen.findByRole('listitem')

    await user.click(screen.getByRole('button', { name: 'Resolve' }))
    const submit = screen.getAllByRole('button', { name: 'Resolve' }).at(-1)!

    expect(submit).toBeDisabled()
    expect(move).not.toHaveBeenCalled()
  })

  it('offers nothing on a closed discrepancy', async () => {
    vi.spyOn(client, 'fetchDiscrepancies').mockResolvedValue(
      page([discrepancy({ status: 'resolved', is_open: false, available_transitions: [] })]),
    )
    render(<DiscrepancyConsole />)
    const item = await screen.findByRole('listitem')

    expect(within(item).queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('the list', () => {
  it('shows open discrepancies by default', async () => {
    render(<DiscrepancyConsole />)
    await screen.findByRole('listitem')

    expect(client.fetchDiscrepancies).toHaveBeenCalledWith({ open: true })
  })

  it('can include closed ones', async () => {
    const user = userEvent.setup()
    render(<DiscrepancyConsole />)
    await screen.findByRole('listitem')

    await user.click(screen.getByLabelText('Include closed'))

    await waitFor(() => expect(client.fetchDiscrepancies).toHaveBeenLastCalledWith({}))
  })

  it('links to the evidence behind each claim', async () => {
    render(<DiscrepancyConsole />)
    const item = await screen.findByRole('listitem')

    expect(within(item).getByRole('link', { name: 'Evidence' })).toHaveAttribute(
      'href',
      'https://www.upsc.gov.in/notice',
    )
  })

  it('reports a failure to load rather than showing an empty list', async () => {
    vi.spyOn(client, 'fetchDiscrepancies').mockRejectedValue(new Error('nope'))
    render(<DiscrepancyConsole />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
  })

  it('announces what just happened for keyboard users', async () => {
    vi.spyOn(client, 'transitionDiscrepancy').mockResolvedValue(
      discrepancy({ status: 'confirmed' }),
    )
    const user = userEvent.setup()
    render(<DiscrepancyConsole />)
    await screen.findByRole('listitem')

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByRole('status')).toHaveTextContent(/confirmed/i)
  })
})
