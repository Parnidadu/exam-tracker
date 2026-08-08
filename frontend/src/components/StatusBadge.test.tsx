import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { roleForValue, StatusBadge } from './StatusBadge'

function renderBadge(props: Partial<Parameters<typeof StatusBadge>[0]> = {}) {
  render(
    <StatusBadge
      track="conduct"
      value="conducted"
      isVerificationFresh
      verifiedAt="2026-06-01T00:00:00Z"
      {...props}
    />,
  )
  return screen.getByTestId('status-badge')
}

describe('StatusBadge', () => {
  it('shows the track value', () => {
    expect(renderBadge({ value: 'postponed' })).toHaveTextContent('postponed')
  })

  it('shows a freshness indicator when the verification is fresh', () => {
    const badge = renderBadge({ isVerificationFresh: true })
    expect(badge).toHaveTextContent('fresh')
    expect(badge).toHaveAttribute('data-freshness', 'fresh')
  })

  it('shows a stale indicator once the verification has aged out', () => {
    const badge = renderBadge({ isVerificationFresh: false })
    expect(badge).toHaveTextContent('stale')
    expect(badge).toHaveAttribute('data-freshness', 'stale')
  })

  it('shows unverified when the track has never been human-verified', () => {
    const badge = renderBadge({ verifiedAt: null, isVerificationFresh: false })
    expect(badge).toHaveTextContent('unverified')
    expect(badge).toHaveAttribute('data-freshness', 'unverified')
  })

  it('never conveys status by colour alone - a text role accompanies every badge', () => {
    // The role name is present in the accessible text, so the badge survives
    // greyscale print, colour-blind readers and forced-colors mode.
    renderBadge({ track: 'conduct', value: 'cancelled' })
    expect(screen.getByText('critical:')).toBeInTheDocument()
  })

  it('renders a distinct glyph per role, so shape carries meaning too', () => {
    const glyphs = new Set<string>()
    for (const [track, value] of [
      ['conduct', 'conducted'],
      ['conduct', 'postponed'],
      ['conduct', 'cancelled'],
      ['integrity', 'disputed'],
      ['conduct', 'something-unmapped'],
    ] as const) {
      const { container } = render(
        <StatusBadge track={track} value={value} verifiedAt={null} />,
      )
      const glyph = container.querySelector('[aria-hidden="true"]')?.textContent ?? ''
      glyphs.add(glyph)
    }
    expect(glyphs.size).toBe(5)
  })

  it('puts the value text in an ink token, not the status colour', () => {
    const badge = renderBadge({ value: 'conducted' })
    const valueText = screen.getByText('conducted')
    expect(valueText).toHaveStyle({ color: 'var(--text-primary)' })
    expect(badge).toHaveAttribute('data-role', 'good')
  })
})

describe('roleForValue', () => {
  it('maps each track’s values to a status role', () => {
    expect(roleForValue('conduct', 'conducted')).toBe('good')
    expect(roleForValue('conduct', 'postponed')).toBe('warning')
    expect(roleForValue('conduct', 'cancelled')).toBe('critical')
    expect(roleForValue('result', 'declared')).toBe('good')
    expect(roleForValue('result', 'withheld')).toBe('serious')
    expect(roleForValue('integrity', 'compromised')).toBe('critical')
  })

  it('falls back to neutral for values the backend has not seen before', () => {
    // `value` is free text server-side, so an unknown string must not be
    // coloured as though it meant something.
    expect(roleForValue('conduct', 'rescheduled-twice')).toBe('neutral')
    expect(roleForValue('conduct', '')).toBe('neutral')
  })

  it('is tolerant of casing and stray whitespace', () => {
    expect(roleForValue('conduct', '  Conducted ')).toBe('good')
  })
})