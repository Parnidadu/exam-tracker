import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { fetchCalendar } from '../../api/client'
import type { CalendarEntry } from '../../api/types'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function monthKey(d: Date): string {
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`
}

function parseMonth(value: string | null, fallback: Date): { year: number; month: number } {
  const match = value?.match(/^(\d{4})-(\d{2})$/)
  if (match) {
    const month = Number(match[2])
    if (month >= 1 && month <= 12) return { year: Number(match[1]), month }
  }
  return { year: fallback.getUTCFullYear(), month: fallback.getUTCMonth() + 1 }
}

function monthLabel(year: number, month: number): string {
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString('en-GB', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

/** Days of the month, padded with leading blanks so the 1st sits under its weekday. */
function buildGrid(year: number, month: number): (string | null)[] {
  const first = new Date(Date.UTC(year, month - 1, 1))
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  // getUTCDay() is 0=Sun; shift so Monday starts the week.
  const leading = (first.getUTCDay() + 6) % 7

  const cells: (string | null)[] = Array(leading).fill(null)
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push(`${year}-${pad(month)}-${pad(day)}`)
  }
  return cells
}

function shiftMonth(year: number, month: number, delta: number) {
  const shifted = new Date(Date.UTC(year, month - 1 + delta, 1))
  return { year: shifted.getUTCFullYear(), month: shifted.getUTCMonth() + 1 }
}

export function CalendarView() {
  const [params, setParams] = useSearchParams()
  const today = useMemo(() => new Date(), [])

  const { year, month } = parseMonth(params.get('month'), today)
  const selectedDate = params.get('date')

  const [entries, setEntries] = useState<CalendarEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const monthValue = `${year}-${pad(month)}`

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    fetchCalendar(monthValue)
      .then((data) => {
        if (active) setEntries(data)
      })
      .catch(() => {
        if (active) setError('Could not load the calendar.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [monthValue])

  const byDate = useMemo(() => {
    const map = new Map<string, CalendarEntry[]>()
    for (const entry of entries) {
      const bucket = map.get(entry.date)
      if (bucket) bucket.push(entry)
      else map.set(entry.date, [entry])
    }
    return map
  }, [entries])

  const cells = useMemo(() => buildGrid(year, month), [year, month])
  const todayKey = monthKey(today) === monthValue ? today.toISOString().slice(0, 10) : null

  function goToMonth(delta: number) {
    const next = shiftMonth(year, month, delta)
    setParams(
      (previous) => {
        const nextParams = new URLSearchParams(previous)
        nextParams.set('month', `${next.year}-${pad(next.month)}`)
        // A selected day from the old month is meaningless in the new one.
        nextParams.delete('date')
        return nextParams
      },
      { replace: true },
    )
  }

  function selectDate(date: string) {
    setParams(
      (previous) => {
        const nextParams = new URLSearchParams(previous)
        if (nextParams.get('date') === date) nextParams.delete('date')
        else nextParams.set('date', date)
        return nextParams
      },
      { replace: true },
    )
  }

  const selectedEntries = selectedDate ? (byDate.get(selectedDate) ?? []) : []

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
          Exam calendar
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => goToMonth(-1)}
            aria-label="Previous month"
            className="rounded border border-gray-300 px-2 py-1 text-sm"
          >
            ←
          </button>
          <span data-testid="month-label" className="min-w-[9rem] text-center text-sm">
            {monthLabel(year, month)}
          </span>
          <button
            type="button"
            onClick={() => goToMonth(1)}
            aria-label="Next month"
            className="rounded border border-gray-300 px-2 py-1 text-sm"
          >
            →
          </button>
        </div>
      </div>

      {loading && <p>Loading calendar…</p>}
      {error && <p role="alert">{error}</p>}

      {!loading && !error && (
        <>
          {/* 7 columns at every width. Cells shrink rather than reflow, so the
              grid stays a month grid down to 360px; the day list below is
              where detail goes, instead of cramming it into a 45px cell. */}
          <div role="grid" aria-label={`${monthLabel(year, month)} calendar`}>
            <div role="row" className="grid grid-cols-7 gap-px">
              {WEEKDAYS.map((day) => (
                <div
                  key={day}
                  role="columnheader"
                  className="py-1 text-center text-[11px] uppercase"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  <span aria-hidden="true">{day.charAt(0)}</span>
                  <span className="sr-only">{day}</span>
                </div>
              ))}
            </div>

            <div role="row" className="grid grid-cols-7 gap-px">
              {cells.map((date, index) => {
                if (!date) {
                  return <div key={`pad-${index}`} role="gridcell" aria-hidden="true" />
                }
                const dayEntries = byDate.get(date) ?? []
                const isSelected = date === selectedDate
                const day = Number(date.slice(8, 10))

                return (
                  <div key={date} role="gridcell" className="p-px">
                    <button
                      type="button"
                      onClick={() => selectDate(date)}
                      data-testid={`day-${date}`}
                      data-count={dayEntries.length}
                      aria-pressed={isSelected}
                      aria-label={`${day} ${monthLabel(year, month)}, ${dayEntries.length} ${
                        dayEntries.length === 1 ? 'event' : 'events'
                      }`}
                      className={`flex aspect-square w-full min-w-0 flex-col items-center justify-center rounded border text-xs ${
                        isSelected ? 'border-gray-900 bg-gray-100' : 'border-gray-200'
                      }`}
                    >
                      <span
                        className={date === todayKey ? 'font-bold underline' : ''}
                        style={{ color: 'var(--text-primary)' }}
                      >
                        {day}
                      </span>
                      {/* A count, not just a dot: the number survives greyscale
                          and tells you how much is on that day. */}
                      {dayEntries.length > 0 && (
                        <span
                          aria-hidden="true"
                          className="text-[10px] leading-none"
                          style={{ color: 'var(--status-good)' }}
                        >
                          ●{dayEntries.length > 1 ? dayEntries.length : ''}
                        </span>
                      )}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          <section aria-label="Selected day" className="mt-4">
            {!selectedDate && (
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                Select a date to see that day’s exams.
              </p>
            )}
            {selectedDate && selectedEntries.length === 0 && (
              <p role="status" className="text-sm">
                Nothing scheduled on {selectedDate}.
              </p>
            )}
            {selectedDate && selectedEntries.length > 0 && (
              <ul aria-label={`Exams on ${selectedDate}`} className="space-y-2">
                {selectedEntries.map((entry) => (
                  <li
                    key={`${entry.exam_slug}-${entry.stage_type}-${entry.milestone}`}
                    className="rounded border border-gray-200 px-3 py-2 text-sm"
                  >
                    <Link to={`/exams/${entry.exam_slug}`} className="font-medium underline">
                      {entry.exam_name}
                    </Link>
                    <div style={{ color: 'var(--text-secondary)' }}>
                      {entry.board_code} · {entry.stage_type} · {entry.milestone_label}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  )
}
