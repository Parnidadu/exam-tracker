import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchQueue, verifyStage } from '../../api/client'
import { REASON_LABELS, TRACK_VALUES, type QueueItem } from '../../api/types'
import { useQueueKeys } from './useQueueKeys'

const SHORTCUTS: readonly [string, string][] = [
  ['j / ↓', 'Next item'],
  ['k / ↑', 'Previous item'],
  ['1 2 3', 'Set value'],
  ['e', 'Focus evidence URL'],
  ['Enter', 'Submit current item'],
  ['?', 'Toggle this help'],
]

function isValidUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

export function VerifierConsole() {
  const [items, setItems] = useState<QueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selected, setSelected] = useState(0)
  const [value, setValue] = useState<string | null>(null)
  const [evidenceUrl, setEvidenceUrl] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [cleared, setCleared] = useState(0)
  const [helpOpen, setHelpOpen] = useState(false)

  const evidenceRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let active = true
    fetchQueue()
      .then((page) => {
        if (active) setItems(page.results)
      })
      .catch(() => {
        if (active) setLoadError('Could not load the verification queue.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const current = items[selected]
  const valueOptions = useMemo(
    () => (current ? TRACK_VALUES[current.track] : []),
    [current],
  )

  // Moving to a different item must not carry the previous item's answer
  // over - that would let a mis-keyed Enter file the wrong verification.
  const resetDraft = useCallback(() => {
    setValue(null)
    setEvidenceUrl('')
    setFormError(null)
  }, [])

  const move = useCallback(
    (delta: number) => {
      setSelected((index) => {
        if (items.length === 0) return 0
        const next = Math.min(Math.max(index + delta, 0), items.length - 1)
        if (next !== index) resetDraft()
        return next
      })
    },
    [items.length, resetDraft],
  )

  const pickValue = useCallback(
    (index: number) => {
      const picked = valueOptions[index]
      if (picked) {
        setValue(picked)
        setFormError(null)
      }
    },
    [valueOptions],
  )

  const focusEvidence = useCallback(() => {
    evidenceRef.current?.focus()
  }, [])

  const submit = useCallback(() => {
    if (!current || submitting) return

    if (!value) {
      setFormError('Choose a value (1, 2 or 3) before submitting.')
      return
    }
    if (!evidenceUrl.trim()) {
      setFormError('Evidence URL is required.')
      evidenceRef.current?.focus()
      return
    }
    if (!isValidUrl(evidenceUrl.trim())) {
      setFormError('Evidence URL must be a valid http(s) URL.')
      evidenceRef.current?.focus()
      return
    }

    setSubmitting(true)
    verifyStage(current.exam_stage_id, {
      track: current.track,
      value,
      evidence_url: evidenceUrl.trim(),
    })
      .then(() => {
        // Hand keyboard control back to the shortcut layer. Without this the
        // caret stays in the evidence field, so the next item's 1/2/3/e keys
        // would be typed as text instead of acting as shortcuts.
        evidenceRef.current?.blur()
        setItems((rest) => rest.filter((item) => item.id !== current.id))
        setCleared((n) => n + 1)
        // Stay at the same index: the next item slides up into this slot,
        // so the verifier keeps working without re-navigating.
        setSelected((index) => Math.max(0, Math.min(index, items.length - 2)))
        resetDraft()
      })
      .catch(() => setFormError('Could not submit. Please retry.'))
      .finally(() => setSubmitting(false))
  }, [current, submitting, value, evidenceUrl, items.length, resetDraft])

  const handlers = useMemo(
    () => ({
      onNext: () => move(1),
      onPrev: () => move(-1),
      onPickValue: pickValue,
      onFocusEvidence: focusEvidence,
      onSubmit: submit,
      onToggleHelp: () => setHelpOpen((open) => !open),
    }),
    [move, pickValue, focusEvidence, submit],
  )

  useQueueKeys(handlers)

  if (loading) return <p className="text-gray-600">Loading queue…</p>
  if (loadError) return <p role="alert">{loadError}</p>

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Verifier console</h2>
        <p className="text-sm text-gray-600">
          <span data-testid="remaining">{items.length}</span> remaining ·{' '}
          <span data-testid="cleared">{cleared}</span> cleared
        </p>
      </div>

      <p className="mb-4 text-sm text-gray-500">
        Press <kbd>?</kbd> for keyboard shortcuts.
      </p>

      {helpOpen && (
        <dl className="mb-4 rounded border border-gray-200 p-4 text-sm" aria-label="Shortcuts">
          {SHORTCUTS.map(([keys, description]) => (
            <div key={keys} className="flex gap-3">
              <dt className="w-24 font-mono text-gray-900">{keys}</dt>
              <dd className="text-gray-600">{description}</dd>
            </div>
          ))}
        </dl>
      )}

      {items.length === 0 ? (
        <p role="status">Queue cleared. Nothing left to verify.</p>
      ) : (
        <div className="grid gap-6 md:grid-cols-2">
          <ol aria-label="Verification queue" className="space-y-1">
            {items.map((item, index) => (
              <li
                key={item.id}
                aria-current={index === selected ? 'true' : undefined}
                className={`rounded border px-3 py-2 text-sm ${
                  index === selected
                    ? 'border-gray-900 bg-gray-50'
                    : 'border-gray-200 text-gray-600'
                }`}
              >
                <span className="font-medium text-gray-900">{item.exam_name}</span>{' '}
                <span className="text-gray-500">
                  · {item.stage_type} · {item.track}
                </span>
                <br />
                <span className="text-xs text-gray-500">
                  {REASON_LABELS[item.reason_code]}
                </span>
              </li>
            ))}
          </ol>

          {current && (
            <section aria-label="Verify item" className="rounded border border-gray-200 p-4">
              <h3 className="font-medium text-gray-900">{current.exam_name}</h3>
              <p className="mb-3 text-sm text-gray-600">
                {current.stage_type} · {current.track} ·{' '}
                {REASON_LABELS[current.reason_code]}
              </p>
              <p className="mb-4 text-sm text-gray-600">
                Machine: <code>{current.machine_value || '—'}</code> · Human:{' '}
                <code>{current.human_value || '—'}</code>
              </p>

              <fieldset className="mb-4">
                <legend className="mb-1 text-sm font-medium text-gray-900">Value</legend>
                {valueOptions.map((option, index) => (
                  <label key={option} className="mr-4 text-sm">
                    <input
                      type="radio"
                      name="value"
                      value={option}
                      checked={value === option}
                      onChange={() => pickValue(index)}
                      className="mr-1"
                    />
                    <span className="font-mono text-gray-500">{index + 1}</span> {option}
                  </label>
                ))}
              </fieldset>

              <label className="mb-1 block text-sm font-medium text-gray-900" htmlFor="evidence">
                Evidence URL (required)
              </label>
              <input
                id="evidence"
                ref={evidenceRef}
                type="url"
                required
                value={evidenceUrl}
                onChange={(event) => setEvidenceUrl(event.target.value)}
                placeholder="https://…"
                className="mb-3 w-full rounded border border-gray-300 px-2 py-1 text-sm"
              />

              {formError && (
                <p role="alert" className="mb-3 text-sm text-red-700">
                  {formError}
                </p>
              )}

              <button
                type="button"
                onClick={submit}
                disabled={submitting}
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                Submit (Enter)
              </button>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
