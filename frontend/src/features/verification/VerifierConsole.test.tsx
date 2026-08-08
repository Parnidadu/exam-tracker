import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { QueueItem } from '../../api/types'
import { VerifierConsole } from './VerifierConsole'

// Kept short on purpose: the 20-item test types this character by character,
// so every extra character is 20 more simulated keystrokes in jsdom.
const EVIDENCE = 'https://x.test/1'

function makeItem(id: number): QueueItem {
  return {
    id,
    reason_code: 'machine_changed',
    queue_priority: 1,
    exam_stage_id: 100 + id,
    exam_slug: `exam-${id}`,
    exam_name: `Exam ${id}`,
    stage_type: 'prelims',
    planned_start_date: null,
    track: 'conduct',
    machine_value: 'conducted',
    machine_seen_at: '2026-01-01T00:00:00Z',
    human_value: 'postponed',
    verified_by: '',
    verified_at: null,
    effective_status: 'conducted',
  }
}

function mockApi(count: number) {
  const posts: { url: string; body: unknown }[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) })
        return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            count,
            next: null,
            previous: null,
            results: Array.from({ length: count }, (_, i) => makeItem(i + 1)),
          }),
      })
    }),
  )

  return posts
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('VerifierConsole', () => {
  // Drives ~350 simulated keystrokes through a live React tree, so it needs
  // more than vitest's 5s default - the cost is real work, not a hang.
  it('lets a verifier clear a 20-item queue using the keyboard only', { timeout: 60_000 }, async () => {
    const posts = mockApi(20)
    const user = userEvent.setup()
    render(<VerifierConsole />)

    await screen.findByLabelText('Verification queue')
    expect(screen.getByTestId('remaining')).toHaveTextContent('20')

    // No pointer events anywhere in this loop - keyboard only.
    for (let i = 0; i < 20; i++) {
      await user.keyboard('1') // pick a value
      await user.keyboard('e') // focus evidence field
      await user.keyboard(EVIDENCE) // type the URL
      await user.keyboard('{Enter}') // submit
      await waitFor(() =>
        expect(screen.getByTestId('cleared')).toHaveTextContent(String(i + 1)),
      )
    }

    expect(posts).toHaveLength(20)
    expect(await screen.findByRole('status')).toHaveTextContent('Queue cleared')
    expect(screen.getByTestId('remaining')).toHaveTextContent('0')
  })

  it('refuses to submit without an evidence URL', async () => {
    const posts = mockApi(3)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    await screen.findByLabelText('Verification queue')

    await user.keyboard('1')
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent('Evidence URL is required')
    expect(posts).toHaveLength(0)
    expect(screen.getByTestId('remaining')).toHaveTextContent('3')
  })

  it('refuses to submit a malformed evidence URL', async () => {
    const posts = mockApi(3)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    await screen.findByLabelText('Verification queue')

    await user.keyboard('1')
    await user.keyboard('e')
    await user.keyboard('not-a-url')
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent('valid http(s) URL')
    expect(posts).toHaveLength(0)
  })

  it('refuses to submit without a value chosen', async () => {
    const posts = mockApi(3)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    await screen.findByLabelText('Verification queue')

    await user.keyboard('e')
    await user.keyboard(EVIDENCE)
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a value')
    expect(posts).toHaveLength(0)
  })

  it('moves the selection with j/k and with arrow keys', async () => {
    mockApi(3)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    const list = await screen.findByLabelText('Verification queue')

    const selected = () =>
      within(list).getAllByRole('listitem').findIndex((li) => li.getAttribute('aria-current'))

    expect(selected()).toBe(0)
    await user.keyboard('j')
    expect(selected()).toBe(1)
    await user.keyboard('{ArrowDown}')
    expect(selected()).toBe(2)
    await user.keyboard('k')
    expect(selected()).toBe(1)
    await user.keyboard('{ArrowUp}')
    expect(selected()).toBe(0)
  })

  it('does not treat typed characters in the URL field as shortcuts', async () => {
    mockApi(3)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    const list = await screen.findByLabelText('Verification queue')

    await user.keyboard('e')
    // "jjj" and "2" would navigate / change the value if the handler
    // did not stand down while the caret is in a text field.
    await user.keyboard('https://x.test/jjj2')

    expect(screen.getByLabelText(/Evidence URL/)).toHaveValue('https://x.test/jjj2')
    const stillFirst = within(list)
      .getAllByRole('listitem')[0]
      .getAttribute('aria-current')
    expect(stillFirst).toBe('true')
  })

  it('clears the draft when moving to another item', async () => {
    mockApi(3)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    await screen.findByLabelText('Verification queue')

    await user.keyboard('1')
    await user.keyboard('e')
    await user.keyboard(EVIDENCE)
    expect(screen.getByLabelText(/Evidence URL/)).toHaveValue(EVIDENCE)

    await user.keyboard('{Escape}')
    await user.keyboard('j')

    expect(screen.getByLabelText(/Evidence URL/)).toHaveValue('')
    expect(screen.getByRole('radio', { name: /conducted/ })).not.toBeChecked()
  })

  it('sends the chosen value, track and evidence URL to the API', async () => {
    const posts = mockApi(1)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    await screen.findByLabelText('Verification queue')

    await user.keyboard('2') // "postponed" for the conduct track
    await user.keyboard('e')
    await user.keyboard(EVIDENCE)
    await user.keyboard('{Enter}')

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].url).toBe('/api/stages/101/verify/')
    expect(posts[0].body).toEqual({
      track: 'conduct',
      value: 'postponed',
      evidence_url: EVIDENCE,
    })
  })

  it('toggles the shortcut help with ?', async () => {
    mockApi(2)
    const user = userEvent.setup()
    render(<VerifierConsole />)
    await screen.findByLabelText('Verification queue')

    expect(screen.queryByLabelText('Shortcuts')).not.toBeInTheDocument()
    await user.keyboard('?')
    expect(screen.getByLabelText('Shortcuts')).toBeInTheDocument()
    await user.keyboard('?')
    expect(screen.queryByLabelText('Shortcuts')).not.toBeInTheDocument()
  })
})
