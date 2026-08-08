import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { fetchBoards, fetchExams } from '../../api/client'
import {
  EMPTY_FILTERS,
  TRACK_VALUES,
  type Board,
  type ExamFilters,
  type ExamSummary,
} from '../../api/types'

const SEARCH_DEBOUNCE_MS = 250

const FILTER_KEYS = Object.keys(EMPTY_FILTERS) as (keyof ExamFilters)[]

/** The URL is the single source of truth for filter state. */
function readFilters(params: URLSearchParams): ExamFilters {
  const filters = { ...EMPTY_FILTERS }
  for (const key of FILTER_KEYS) {
    filters[key] = params.get(key) ?? ''
  }
  return filters
}

export function ExamList() {
  const [params, setParams] = useSearchParams()
  const filters = useMemo(() => readFilters(params), [params])

  const [boards, setBoards] = useState<Board[]>([])
  const [exams, setExams] = useState<ExamSummary[]>([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // The search box keeps its own value so typing stays responsive; it is
  // pushed into the URL on a short debounce rather than per keystroke.
  const [searchDraft, setSearchDraft] = useState(filters.search)
  const searchDraftRef = useRef(searchDraft)
  searchDraftRef.current = searchDraft

  useEffect(() => {
    fetchBoards()
      .then(setBoards)
      .catch(() => setBoards([]))
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    fetchExams(filters)
      .then((page) => {
        if (!active) return
        setExams(page.results)
        setCount(page.count)
      })
      .catch(() => {
        if (active) setError('Could not load exams.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [filters])

  // Keep the box in step when the URL changes from outside (back button,
  // a shared link, Clear all) without clobbering what's being typed.
  useEffect(() => {
    if (filters.search !== searchDraftRef.current) setSearchDraft(filters.search)
  }, [filters.search])

  const setFilter = useCallback(
    (key: keyof ExamFilters, value: string) => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous)
          if (value) next.set(key, value)
          else next.delete(key)
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  useEffect(() => {
    if (searchDraft === filters.search) return
    const timer = setTimeout(() => setFilter('search', searchDraft), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [searchDraft, filters.search, setFilter])

  const activeCount = FILTER_KEYS.filter((key) => filters[key]).length

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold text-gray-900">Exams</h2>

      <form
        aria-label="Filters"
        className="mb-6 grid gap-3 md:grid-cols-3"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium text-gray-900">Search by name</span>
          <input
            type="search"
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            placeholder="e.g. Civil Services"
            className="w-full rounded border border-gray-300 px-2 py-1"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-gray-900">Board</span>
          <select
            value={filters.board}
            onChange={(event) => setFilter('board', event.target.value)}
            className="w-full rounded border border-gray-300 px-2 py-1"
          >
            <option value="">All boards</option>
            {boards.map((board) => (
              <option key={board.id} value={board.code}>
                {board.name}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-gray-900">Conduct status</span>
          <select
            value={filters.conduct_status}
            onChange={(event) => setFilter('conduct_status', event.target.value)}
            className="w-full rounded border border-gray-300 px-2 py-1"
          >
            <option value="">Any</option>
            {TRACK_VALUES.conduct.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-gray-900">Result status</span>
          <select
            value={filters.result_status}
            onChange={(event) => setFilter('result_status', event.target.value)}
            className="w-full rounded border border-gray-300 px-2 py-1"
          >
            <option value="">Any</option>
            {TRACK_VALUES.result.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-gray-900">From date</span>
          <input
            type="date"
            value={filters.start_date}
            onChange={(event) => setFilter('start_date', event.target.value)}
            className="w-full rounded border border-gray-300 px-2 py-1"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-gray-900">To date</span>
          <input
            type="date"
            value={filters.end_date}
            onChange={(event) => setFilter('end_date', event.target.value)}
            className="w-full rounded border border-gray-300 px-2 py-1"
          />
        </label>
      </form>

      <div className="mb-3 flex items-baseline gap-3 text-sm text-gray-600">
        <span data-testid="result-count">
          {count} {count === 1 ? 'exam' : 'exams'}
        </span>
        {activeCount > 0 && (
          <button
            type="button"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
            className="underline"
          >
            Clear all filters
          </button>
        )}
      </div>

      {loading && <p>Loading exams…</p>}
      {error && <p role="alert">{error}</p>}

      {!loading && !error && exams.length === 0 && (
        <p role="status">No exams match these filters.</p>
      )}

      {!loading && !error && exams.length > 0 && (
        <ul aria-label="Exams" className="space-y-2">
          {exams.map((exam) => (
            <li key={exam.id} className="rounded border border-gray-200 px-3 py-2">
              <Link to={`/exams/${exam.slug}`} className="font-medium text-gray-900 underline">
                {exam.name}
              </Link>
              <div className="text-sm text-gray-600">
                {exam.board.name} · {exam.code} · {exam.cycle_year} · {exam.category}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
