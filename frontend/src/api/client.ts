import type {
  ExamDetail,
  Paginated,
  QueueItem,
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
