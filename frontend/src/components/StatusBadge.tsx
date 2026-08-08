import type { Track } from '../api/types'

/**
 * Reserved status roles. Kept separate from any series/categorical colour
 * so a status can never impersonate a data series.
 */
export type StatusRole = 'good' | 'warning' | 'serious' | 'critical' | 'neutral'

interface RoleStyle {
  /** Distinct glyph per role - the non-colour channel that does the work
   *  for colour-blind readers, greyscale print and forced-colors. */
  glyph: string
  /** Spoken name, so the role is never conveyed by shape or hue alone. */
  label: string
  colorVar: string
}

const ROLE_STYLES: Record<StatusRole, RoleStyle> = {
  good: { glyph: '✓', label: 'good', colorVar: 'var(--status-good)' },
  warning: { glyph: '!', label: 'warning', colorVar: 'var(--status-warning)' },
  serious: { glyph: '▲', label: 'serious', colorVar: 'var(--status-serious)' },
  critical: { glyph: '✕', label: 'critical', colorVar: 'var(--status-critical)' },
  neutral: { glyph: '–', label: 'unknown', colorVar: 'var(--status-neutral)' },
}

/**
 * Maps a track's value onto a status role.
 *
 * Values are free text on the backend, so anything unrecognised falls back
 * to `neutral` rather than being coloured misleadingly.
 */
const VALUE_ROLES: Record<Track, Record<string, StatusRole>> = {
  conduct: {
    conducted: 'good',
    postponed: 'warning',
    cancelled: 'critical',
  },
  result: {
    declared: 'good',
    awaited: 'warning',
    withheld: 'serious',
  },
  integrity: {
    clean: 'good',
    disputed: 'serious',
    compromised: 'critical',
  },
}

export function roleForValue(track: Track, value: string): StatusRole {
  return VALUE_ROLES[track]?.[value.trim().toLowerCase()] ?? 'neutral'
}

export interface StatusBadgeProps {
  track: Track
  value: string
  /** From the API (StatusTrack.is_verification_fresh), so the 14-day
   *  staleness rule is never re-implemented here. */
  isVerificationFresh?: boolean
  /** Null when the track has never been human-verified. */
  verifiedAt?: string | null
}

function freshnessText(
  verifiedAt: string | null | undefined,
  isFresh: boolean | undefined,
): { label: string; glyph: string; title: string } {
  if (!verifiedAt) {
    return {
      glyph: '◌',
      label: 'unverified',
      title: 'No human verification recorded for this track.',
    }
  }
  if (isFresh) {
    return {
      glyph: '◉',
      label: 'fresh',
      title: `Human-verified on ${verifiedAt.slice(0, 10)} and still within the freshness window.`,
    }
  }
  return {
    glyph: '◍',
    label: 'stale',
    title: `Last human-verified on ${verifiedAt.slice(0, 10)}; past the freshness window, so the machine value applies.`,
  }
}

export function StatusBadge({
  track,
  value,
  isVerificationFresh,
  verifiedAt,
}: StatusBadgeProps) {
  const role = roleForValue(track, value)
  const style = ROLE_STYLES[role]
  const freshness = freshnessText(verifiedAt, isVerificationFresh)
  const shown = value.trim() || 'unknown'

  return (
    <span
      data-testid="status-badge"
      data-role={role}
      data-freshness={freshness.label}
      className="inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-sm"
      style={{ borderColor: style.colorVar }}
    >
      {/* Colour rides on this mark only. aria-hidden because the adjacent
          visually-hidden text already names the role for screen readers. */}
      <span aria-hidden="true" style={{ color: style.colorVar }} className="font-bold">
        {style.glyph}
      </span>
      <span className="sr-only">{style.label}:</span>

      {/* The value wears an ink token, never the status colour - that keeps
          it legible regardless of the role's contrast against the surface. */}
      <span style={{ color: 'var(--text-primary)' }}>{shown}</span>

      <span aria-hidden="true" style={{ color: 'var(--text-secondary)' }}>
        ·
      </span>
      <span
        title={freshness.title}
        style={{ color: 'var(--text-secondary)' }}
        className="inline-flex items-center gap-1 text-xs"
      >
        <span aria-hidden="true">{freshness.glyph}</span>
        {freshness.label}
      </span>
    </span>
  )
}