import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { fetchExamDetail, fetchExamVerifications } from '../../api/client'
import type { ExamDetail as ExamDetailData, Track, VerificationRecord } from '../../api/types'

/** Key a history bucket by the stage + track it belongs to. */
function bucketKey(stageId: number, track: Track): string {
  return `${stageId}:${track}`
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(0, 16).replace('T', ' ')
}

function TrackHistory({ records }: { records: VerificationRecord[] }) {
  if (records.length === 0) {
    return <p className="text-sm text-gray-500">No verifications recorded yet.</p>
  }

  return (
    <ol className="space-y-2">
      {records.map((record) => (
        <li key={record.id} className="border-l-2 border-gray-200 pl-3 text-sm">
          <div>
            <span className="font-medium text-gray-900">{record.value}</span>{' '}
            <time dateTime={record.timestamp} className="text-gray-500">
              {formatTimestamp(record.timestamp)}
            </time>
          </div>
          <div className="text-gray-600">
            {record.actor ? (
              <span>by {record.actor}</span>
            ) : (
              <span className="text-gray-400">by a verifier (sign in to see who)</span>
            )}
            {record.evidence_url && (
              <>
                {' · '}
                <a href={record.evidence_url} className="underline" rel="noreferrer">
                  evidence
                </a>
              </>
            )}
          </div>
          {record.note && <p className="text-gray-600">{record.note}</p>}
        </li>
      ))}
    </ol>
  )
}

export function ExamDetail() {
  const { slug = '' } = useParams()
  const [exam, setExam] = useState<ExamDetailData | null>(null)
  const [records, setRecords] = useState<VerificationRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)

    Promise.all([fetchExamDetail(slug), fetchExamVerifications(slug)])
      .then(([detail, history]) => {
        if (!active) return
        setExam(detail)
        setRecords(history.results)
      })
      .catch(() => {
        if (active) setError('Could not load this exam.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
    }
  }, [slug])

  // Group once, so each track renders only the verifications that belong
  // to it rather than filtering the whole list per track.
  const historyByTrack = useMemo(() => {
    const buckets = new Map<string, VerificationRecord[]>()
    for (const record of records) {
      const key = bucketKey(record.exam_stage_id, record.track)
      const bucket = buckets.get(key)
      if (bucket) bucket.push(record)
      else buckets.set(key, [record])
    }
    return buckets
  }, [records])

  if (loading) return <p>Loading exam…</p>
  if (error) return <p role="alert">{error}</p>
  if (!exam) return null

  return (
    <article>
      <h2 className="text-lg font-semibold text-gray-900">{exam.name}</h2>
      <p className="mb-6 text-sm text-gray-600">
        {exam.board.name} · {exam.code} · {exam.cycle_year} · {exam.category}
      </p>

      {exam.stages.length === 0 && <p className="text-gray-600">No stages recorded yet.</p>}

      {exam.stages.map((stage) => (
        <section key={stage.id} aria-label={`Stage ${stage.stage_type}`} className="mb-8">
          <h3 className="mb-1 font-medium text-gray-900 capitalize">
            {stage.stage_type}{' '}
            <span className="text-sm font-normal text-gray-500">#{stage.sequence}</span>
          </h3>

          {stage.status_tracks.length === 0 ? (
            <p className="text-sm text-gray-500">No status tracks yet.</p>
          ) : (
            <div className="space-y-4">
              {stage.status_tracks.map((track) => (
                <div
                  key={track.track}
                  aria-label={`${stage.stage_type} ${track.track} track`}
                  className="rounded border border-gray-200 p-3"
                >
                  <div className="mb-2 flex flex-wrap items-baseline gap-x-3">
                    <span className="font-medium text-gray-900 capitalize">{track.track}</span>
                    <span className="text-sm text-gray-600">
                      current:{' '}
                      <strong data-testid={`current-${stage.id}-${track.track}`}>
                        {track.effective_status || '—'}
                      </strong>
                    </span>
                    <span className="text-xs text-gray-500">
                      machine: {track.machine_value || '—'} · human:{' '}
                      {track.human_value || '—'}
                    </span>
                  </div>

                  <h4 className="mb-1 text-xs font-medium uppercase tracking-wide text-gray-500">
                    Verification history
                  </h4>
                  <TrackHistory records={historyByTrack.get(bucketKey(stage.id, track.track)) ?? []} />
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </article>
  )
}
