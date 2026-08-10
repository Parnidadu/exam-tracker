import type {
  Board,
  CalendarEntry,
  Discrepancy,
  ExamDetail,
  ExamFilters,
  ExamSummary,
  OpenDiscrepancyPayload,
  Paginated,
  QueueItem,
  TransitionPayload,
  VerificationRecord,
  VerifyPayload,
} from './types'

/**
 * Django's SessionAuthentication enforces CSRF on unsafe methods, so POSTs
 * must echo the csrftoken cookie back in the X-CSRFToken header.
 */
function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)
  return match ? decodeURIComponent(match[1]) : ''
}

export class ApiError extends Error {
  // Declared explicitly rather than as a constructor parameter property:
  // this project's tsconfig sets erasableSyntaxOnly, which disallows those.
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(init.method && init.method !== 'GET' ? { 'X-CSRFToken': csrfToken() } : {}),
      ...init.headers,
    },
  })

  if (!response.ok) {
    throw new ApiError(`Request failed (${response.status})`, response.status)
  }
  return (await response.json()) as T
}

export function fetchQueue(): Promise<Paginated<QueueItem>> {
  return request<Paginated<QueueItem>>('/api/verification-queue/')
}

export function fetchBoards(): Promise<Board[]> {
  return request<Board[]>('/api/boards/')
}

export function fetchExams(
  filters: Partial<ExamFilters>,
  page = 1,
): Promise<Paginated<ExamSummary>> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  if (page > 1) params.set('page', String(page))
  const query = params.toString()
  return request<Paginated<ExamSummary>>(`/api/exams/${query ? `?${query}` : ''}`)
}

export function fetchExamDetail(slug: string): Promise<ExamDetail> {
  return request<ExamDetail>(`/api/exams/${encodeURIComponent(slug)}/`)
}

export function fetchExamVerifications(
  slug: string,
): Promise<Paginated<VerificationRecord>> {
  return request<Paginated<VerificationRecord>>(
    `/api/exams/${encodeURIComponent(slug)}/verifications/`,
  )
}

export function verifyStage(stageId: number, payload: VerifyPayload): Promise<unknown> {
  return request(`/api/stages/${stageId}/verify/`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchCalendar(month: string): Promise<CalendarEntry[]> {
  return request<CalendarEntry[]>(`/api/calendar/?month=${encodeURIComponent(month)}`)
}

export function fetchDiscrepancies(
  params: { open?: boolean; status?: string } = {},
): Promise<Paginated<Discrepancy>> {
  const query = new URLSearchParams()
  if (params.open) query.set('open', 'true')
  if (params.status) query.set('status', params.status)
  const suffix = query.toString()
  return request<Paginated<Discrepancy>>(
    `/api/discrepancies/${suffix ? `?${suffix}` : ''}`,
  )
}

export function openDiscrepancy(payload: OpenDiscrepancyPayload): Promise<Discrepancy> {
  return request<Discrepancy>('/api/discrepancies/', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateDiscrepancy(
  id: number,
  payload: Partial<OpenDiscrepancyPayload>,
): Promise<Discrepancy> {
  return request<Discrepancy>(`/api/discrepancies/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function transitionDiscrepancy(
  id: number,
  payload: TransitionPayload,
): Promise<Discrepancy> {
  return request<Discrepancy>(`/api/discrepancies/${id}/transition/`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
