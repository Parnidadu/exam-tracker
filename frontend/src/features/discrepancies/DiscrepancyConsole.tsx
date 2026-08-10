import { useEffect, useMemo, useState } from 'react'

import {
  fetchDiscrepancies,
  fetchExamDetail,
  fetchExams,
  openDiscrepancy,
  transitionDiscrepancy,
} from '../../api/client'
import {
  DISCREPANCY_SEVERITIES,
  DISCREPANCY_STATUS_LABELS,
  DISCREPANCY_TYPE_LABELS,
  type Discrepancy,
  type DiscrepancySeverity,
  type DiscrepancyStatus,
  type DiscrepancyType,
  type ExamStageDetail,
  type ExamSummary,
} from '../../api/types'

/**
 * Checked before the request rather than only after it comes back 400.
 * The evidence URL is the one field this whole screen exists to insist
 * on, and telling someone it was missing *after* a round trip is a worse
 * way to say something they could have been told immediately.
 */
function isValidUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

const TYPE_OPTIONS = Object.entries(DISCREPANCY_TYPE_LABELS) as [DiscrepancyType, string][]

/** Wording for each move, so a button says what it does rather than
 * naming the state it lands in. */
const TRANSITION_LABELS: Record<DiscrepancyStatus, string> = {
  reported: 'Reopen',
  confirmed: 'Confirm',
  resolved: 'Resolve',
  dismissed: 'Dismiss',
}

/** Terminal moves need a reason; confirming does not. */
const NEEDS_NOTE: readonly DiscrepancyStatus[] = ['resolved', 'dismissed']

export function DiscrepancyConsole() {
  const [items, setItems] = useState<Discrepancy[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showClosed, setShowClosed] = useState(false)

  const [exams, setExams] = useState<ExamSummary[]>([])
  const [examSlug, setExamSlug] = useState('')
  const [stages, setStages] = useState<ExamStageDetail[]>([])
  const [stageId, setStageId] = useState('')
  const [type, setType] = useState<DiscrepancyType>('postponement')
  const [severity, setSeverity] = useState<DiscrepancySeverity>('medium')
  const [description, setDescription] = useState('')
  const [evidenceUrl, setEvidenceUrl] = useState('')
  const [occurredOn, setOccurredOn] = useState('')

  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const [noteFor, setNoteFor] = useState<{ id: number; status: DiscrepancyStatus } | null>(null)
  const [note, setNote] = useState('')

  const reload = useMemo(
    () => () => {
      setLoading(true)
      fetchDiscrepancies(showClosed ? {} : { open: true })
        .then((page) => setItems(page.results))
        .catch(() => setLoadError('Could not load discrepancies.'))
        .finally(() => setLoading(false))
    },
    [showClosed],
  )

  useEffect(reload, [reload])

  useEffect(() => {
    let active = true
    fetchExams({}, 1)
      .then((page) => {
        if (active) setExams(page.results)
      })
      .catch(() => {
        /* The list still works; only the "open one" form needs exams. */
      })
    return () => {
      active = false
    }
  }, [])

  // Stages come from the chosen exam rather than from a flat list of every
  // stage in the database: a verifier picks an exam they know by name, and
  // a global stage dropdown is unusable past a few dozen exams.
  useEffect(() => {
    if (!examSlug) {
      setStages([])
      setStageId('')
      return
    }
    let active = true
    fetchExamDetail(examSlug)
      .then((exam) => {
        if (!active) return
        setStages(exam.stages)
        setStageId(exam.stages.length === 1 ? String(exam.stages[0].id) : '')
      })
      .catch(() => {
        if (active) setStages([])
      })
    return () => {
      active = false
    }
  }, [examSlug])

  async function submitNew(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)

    if (!stageId) {
      setFormError('Choose the exam and stage this affects.')
      return
    }
    if (!description.trim()) {
      setFormError('Describe what happened.')
      return
    }
    if (!isValidUrl(evidenceUrl)) {
      setFormError('An evidence URL is required, and must start with http:// or https://.')
      return
    }

    setSubmitting(true)
    try {
      await openDiscrepancy({
        exam_stage: Number(stageId),
        discrepancy_type: type,
        severity,
        description: description.trim(),
        evidence_url: evidenceUrl.trim(),
        occurred_on: occurredOn || null,
      })
      setDescription('')
      setEvidenceUrl('')
      setOccurredOn('')
      setNotice('Discrepancy opened.')
      reload()
    } catch {
      setFormError('Could not open the discrepancy.')
    } finally {
      setSubmitting(false)
    }
  }

  async function move(id: number, status: DiscrepancyStatus, resolutionNote = '') {
    setNotice(null)
    try {
      await transitionDiscrepancy(id, {
        status,
        ...(resolutionNote ? { resolution_note: resolutionNote } : {}),
      })
      setNoteFor(null)
      setNote('')
      setNotice(`Marked ${DISCREPANCY_STATUS_LABELS[status].toLowerCase()}.`)
      reload()
    } catch {
      setNotice(null)
      setLoadError('Could not update that discrepancy.')
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <h2 className="mb-4 text-lg font-semibold">Discrepancies</h2>

      {/* Announced rather than only shown: a verifier working by keyboard
          would otherwise get no confirmation that anything happened. */}
      <p role="status" aria-live="polite" className="mb-2 text-sm">
        {notice}
      </p>

      <section aria-label="Open a discrepancy" className="mb-8 rounded border border-gray-200 p-4">
        <h3 className="mb-3 font-medium">Open a discrepancy</h3>
        {/*
          noValidate so this component's own checks are the single path.
          Native constraint validation short-circuits submit entirely, which
          means the role="alert" message below never appears - and a native
          tooltip is not announced the way a live region is. The `required`
          attribute stays for the semantics it gives assistive tech.
        */}
        <form onSubmit={submitNew} noValidate className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            Exam
            <select
              className="mt-1 w-full rounded border border-gray-300 p-2"
              value={examSlug}
              onChange={(event) => setExamSlug(event.target.value)}
            >
              <option value="">Choose an exam</option>
              {exams.map((exam) => (
                <option key={exam.slug} value={exam.slug}>
                  {exam.board.code} {exam.name} {exam.cycle_year}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            Stage
            <select
              className="mt-1 w-full rounded border border-gray-300 p-2"
              value={stageId}
              onChange={(event) => setStageId(event.target.value)}
              disabled={!stages.length}
            >
              <option value="">{stages.length ? 'Choose a stage' : 'Choose an exam first'}</option>
              {stages.map((stage) => (
                <option key={stage.id} value={stage.id}>
                  {stage.stage_type}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            Type
            <select
              className="mt-1 w-full rounded border border-gray-300 p-2"
              value={type}
              onChange={(event) => setType(event.target.value as DiscrepancyType)}
            >
              {TYPE_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            Severity
            <select
              className="mt-1 w-full rounded border border-gray-300 p-2"
              value={severity}
              onChange={(event) => setSeverity(event.target.value as DiscrepancySeverity)}
            >
              {DISCREPANCY_SEVERITIES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm sm:col-span-2">
            What happened
            <textarea
              className="mt-1 w-full rounded border border-gray-300 p-2"
              rows={2}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>

          <label className="text-sm sm:col-span-2">
            Evidence URL (required)
            <input
              type="url"
              required
              className="mt-1 w-full rounded border border-gray-300 p-2"
              placeholder="https://…"
              value={evidenceUrl}
              onChange={(event) => setEvidenceUrl(event.target.value)}
            />
          </label>

          <label className="text-sm">
            When it happened (optional)
            <input
              type="date"
              className="mt-1 w-full rounded border border-gray-300 p-2"
              value={occurredOn}
              onChange={(event) => setOccurredOn(event.target.value)}
            />
          </label>

          <div className="sm:col-span-2">
            {formError && (
              <p role="alert" className="mb-2 text-sm font-medium">
                {formError}
              </p>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="rounded border border-gray-400 px-3 py-2 text-sm"
            >
              {submitting ? 'Opening…' : 'Open discrepancy'}
            </button>
          </div>
        </form>
      </section>

      <section aria-label="Discrepancy list">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-medium">{showClosed ? 'All discrepancies' : 'Still open'}</h3>
          <label className="text-sm">
            <input
              type="checkbox"
              className="mr-2"
              checked={showClosed}
              onChange={(event) => setShowClosed(event.target.checked)}
            />
            Include closed
          </label>
        </div>

        {loading && <p>Loading…</p>}
        {loadError && (
          <p role="alert" className="text-sm font-medium">
            {loadError}
          </p>
        )}
        {!loading && !items.length && <p className="text-sm">Nothing to show.</p>}

        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="rounded border border-gray-200 p-4">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <strong>{DISCREPANCY_TYPE_LABELS[item.discrepancy_type]}</strong>
                <span className="text-sm">
                  {item.board_code} {item.exam_name} — {item.stage_type}
                </span>
                <span className="ml-auto text-sm">
                  {DISCREPANCY_STATUS_LABELS[item.status]} · {item.severity}
                </span>
              </div>

              <p className="mt-1 text-sm">{item.description}</p>

              <p className="mt-1 text-sm">
                <a href={item.evidence_url} target="_blank" rel="noopener noreferrer">
                  Evidence
                </a>
                {item.resolution_note && <> · {item.resolution_note}</>}
              </p>

              {/* Only the moves the server says will work. Offering the
                  rest and failing afterwards teaches people to distrust
                  the buttons. */}
              {item.available_transitions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {item.available_transitions.map((status) => (
                    <button
                      key={status}
                      type="button"
                      className="rounded border border-gray-400 px-2 py-1 text-sm"
                      onClick={() =>
                        NEEDS_NOTE.includes(status)
                          ? (setNoteFor({ id: item.id, status }), setNote(''))
                          : move(item.id, status)
                      }
                    >
                      {TRANSITION_LABELS[status]}
                    </button>
                  ))}
                </div>
              )}

              {noteFor?.id === item.id && (
                <form
                  className="mt-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    if (!note.trim()) return
                    move(item.id, noteFor.status, note.trim())
                  }}
                >
                  <label className="text-sm">
                    Why? (required to {TRANSITION_LABELS[noteFor.status].toLowerCase()})
                    <textarea
                      className="mt-1 w-full rounded border border-gray-300 p-2"
                      rows={2}
                      value={note}
                      onChange={(event) => setNote(event.target.value)}
                    />
                  </label>
                  <button
                    type="submit"
                    disabled={!note.trim()}
                    className="mt-1 rounded border border-gray-400 px-2 py-1 text-sm"
                  >
                    {TRANSITION_LABELS[noteFor.status]}
                  </button>
                </form>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
