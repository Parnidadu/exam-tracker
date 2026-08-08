export type ReasonCode =
  | 'machine_changed'
  | 'date_elapsed_no_update'
  | 'stale_verification'

export type Track = 'conduct' | 'result' | 'integrity'

/** Mirrors the backend's QueueItemSerializer (EXT-024). */
export interface QueueItem {
  id: number
  reason_code: ReasonCode
  queue_priority: number
  exam_stage_id: number
  exam_slug: string
  exam_name: string
  stage_type: string
  planned_start_date: string | null
  track: Track
  machine_value: string
  machine_seen_at: string | null
  human_value: string
  verified_by: string
  verified_at: string | null
  effective_status: string
}

/** Mirrors the backend's StatusTrackSerializer, nested in exam detail. */
export interface StatusTrackDetail {
  track: Track
  machine_value: string
  machine_confidence: number | null
  machine_seen_at: string | null
  human_value: string
  verified_by: string
  verified_at: string | null
  effective_status: string
  /** Server-computed, so the staleness window isn't duplicated client-side. */
  is_verification_fresh: boolean
}

export interface ExamStageDetail {
  id: number
  stage_type: string
  sequence: number
  planned_start_date: string | null
  planned_end_date: string | null
  status_tracks: StatusTrackDetail[]
}

export interface ExamDetail {
  id: number
  board: { id: number; name: string; code: string }
  code: string
  name: string
  cycle_year: number
  category: string
  slug: string
  stages: ExamStageDetail[]
}

/** Mirrors VerificationRecordSerializer. `actor` is null for anonymous callers. */
export interface VerificationRecord {
  id: number
  exam_stage_id: number
  stage_type: string
  sequence: number
  track: Track
  value: string
  evidence_url: string
  note: string
  actor: string | null
  timestamp: string
}

export interface Board {
  id: number
  name: string
  code: string
}

/** Mirrors ExamSerializer - the list-view shape, without nested stages. */
export interface ExamSummary {
  id: number
  board: Board
  code: string
  name: string
  cycle_year: number
  category: string
  slug: string
}

/** Query state for the public exam list, mirrored into the URL. */
export interface ExamFilters {
  search: string
  board: string
  conduct_status: string
  result_status: string
  start_date: string
  end_date: string
}

export const EMPTY_FILTERS: ExamFilters = {
  search: '',
  board: '',
  conduct_status: '',
  result_status: '',
  start_date: '',
  end_date: '',
}

export interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

export interface VerifyPayload {
  track: Track
  value: string
  evidence_url: string
  note?: string
}

export const REASON_LABELS: Record<ReasonCode, string> = {
  machine_changed: 'Machine contradicts record',
  date_elapsed_no_update: 'Date passed, no observation',
  stale_verification: 'Verification is stale',
}

/**
 * Values offered per track. The backend stores `value` as free text
 * (StatusTrack/VerificationRecord don't constrain it), so this is the
 * console's own vocabulary - kept to three per track so each maps to a
 * single number key.
 */
export const TRACK_VALUES: Record<Track, readonly string[]> = {
  conduct: ['conducted', 'postponed', 'cancelled'],
  result: ['declared', 'awaited', 'withheld'],
  integrity: ['clean', 'disputed', 'compromised'],
}
