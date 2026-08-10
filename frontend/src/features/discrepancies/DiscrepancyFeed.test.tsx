import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '../../api/client'
import type { Paginated, PublicDiscrepancy } from '../../api/types'
import { DiscrepancyFeed } from './DiscrepancyFeed'

function entry(overrides: Partial<PublicDiscrepancy> = {}): PublicDiscrepancy {
  return {
    id: 1,
    exam_slug: 'upsc-cse-2026',
    exam_name: 'Civil Services Examination',
    board_code: 'UPSC',
    stage_type: 'prelims',
    discrepancy_type: 'postponement',
    type_label: 'Postponement',
    severity: 'medium',
    status: 'confirmed',
    description: 'Postponed by two weeks.',
    evidence_url: 'https://www.upsc.gov.in/notice',
    occurred_on: '2026-01-15',
    resolution_note: '',
    resolved_at: null,
    ...overrides,
  }
}

function page(results: PublicDiscrepancy[]): Paginated<PublicDiscrepancy> {
  return { count: results.length, next: null, previous: null, results }
}

function renderFeed() {
  return render(
    <MemoryRouter>
      <DiscrepancyFeed />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.spyOn(client, 'fetchPublicDiscrepancies').mockResolvedValue(page([entry()]))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the public feed', () => {
  it('reads the public endpoint, not the verifier one', async () => {
    renderFeed()
    await screen.findByRole('heading', { name: 'Recent discrepancies' })

    expect(client.fetchPublicDiscrepancies).toHaveBeenCalled()
  })

  it('names the exam, the stage and what happened', async () => {
    renderFeed()
    const item = await screen.findByRole('listitem')

    expect(within(item).getByText('Postponement')).toBeInTheDocument()
    expect(within(item).getByText(/Postponed by two weeks/)).toBeInTheDocument()
    expect(within(item).getByText(/prelims/)).toBeInTheDocument()
  })

  it('links to the exam it affects', async () => {
    renderFeed()
    const item = await screen.findByRole('listitem')

    expect(within(item).getByRole('link', { name: /Civil Services Examination/ })).toHaveAttribute(
      'href',
      '/exams/upsc-cse-2026',
    )
  })

  it('links the official notice behind the claim', async () => {
    renderFeed()
    const item = await screen.findByRole('listitem')

    const evidence = within(item).getByRole('link', { name: 'Official notice' })
    expect(evidence).toHaveAttribute('href', 'https://www.upsc.gov.in/notice')
    expect(evidence).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('says whether it is resolved or still ongoing, in words', async () => {
    vi.spyOn(client, 'fetchPublicDiscrepancies').mockResolvedValue(
      page([
        entry({ id: 1, status: 'confirmed' }),
        entry({ id: 2, status: 'resolved', resolution_note: 'Re-conducted on 12 March.' }),
      ]),
    )
    renderFeed()
    await screen.findByRole('heading', { name: 'Recent discrepancies' })

    expect(screen.getByText('Confirmed, ongoing')).toBeInTheDocument()
    expect(screen.getByText('Resolved')).toBeInTheDocument()
  })

  it('shows what was done about a resolved one', async () => {
    vi.spyOn(client, 'fetchPublicDiscrepancies').mockResolvedValue(
      page([entry({ status: 'resolved', resolution_note: 'Re-conducted on 12 March.' })]),
    )
    renderFeed()

    expect(await screen.findByText('Re-conducted on 12 March.')).toBeInTheDocument()
  })

  it('renders nothing at all when nothing is wrong', async () => {
    vi.spyOn(client, 'fetchPublicDiscrepancies').mockResolvedValue(page([]))
    const { container } = renderFeed()

    await waitFor(() => expect(client.fetchPublicDiscrepancies).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('stays out of the way when the feed cannot be loaded', async () => {
    // The dashboard's job is the exam list; a broken side panel must not
    // become the most prominent thing on it.
    vi.spyOn(client, 'fetchPublicDiscrepancies').mockRejectedValue(new Error('nope'))
    const { container } = renderFeed()

    await waitFor(() => expect(client.fetchPublicDiscrepancies).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('keeps the dashboard panel short', async () => {
    vi.spyOn(client, 'fetchPublicDiscrepancies').mockResolvedValue(
      page(Array.from({ length: 12 }, (_, index) => entry({ id: index + 1 }))),
    )
    renderFeed()
    await screen.findByRole('heading', { name: 'Recent discrepancies' })

    expect(screen.getAllByRole('listitem')).toHaveLength(5)
  })
})
