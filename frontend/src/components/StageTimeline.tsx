import { TIMELINE_MILESTONES, type ExamStageDetail } from '../api/types'

/** State of a milestone relative to today. */
type MilestoneState = 'passed' | 'upcoming' | 'unannounced'

const STATE_GLYPH: Record<MilestoneState, string> = {
  passed: '●',
  upcoming: '○',
  unannounced: '·',
}

const STATE_LABEL: Record<MilestoneState, string> = {
  passed: 'passed',
  upcoming: 'upcoming',
  unannounced: 'date not announced',
}

function milestoneState(date: string | null, today: Date): MilestoneState {
  if (!date) return 'unannounced'
  // Compare date-only; a milestone dated today counts as reached.
  return date <= today.toISOString().slice(0, 10) ? 'passed' : 'upcoming'
}

function formatDate(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return date
  return parsed.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

export interface StageTimelineProps {
  stage: ExamStageDetail
  /** Injectable so tests aren't tied to the wall clock. */
  today?: Date
}

export function StageTimeline({ stage, today = new Date() }: StageTimelineProps) {
  return (
    <ol
      aria-label={`${stage.stage_type} timeline`}
      data-testid={`timeline-${stage.id}`}
      className="flex flex-col gap-0 sm:flex-row sm:gap-0"
    >
      {TIMELINE_MILESTONES.map(({ key, label }) => {
        const date = stage[key] as string | null
        const state = milestoneState(date, today)

        return (
          <li
            key={key}
            data-testid={`milestone-${stage.id}-${key}`}
            data-state={state}
            className="flex flex-1 items-start gap-2 py-1 sm:flex-col sm:gap-1"
          >
            <span className="flex items-center gap-2 sm:w-full">
              {/* Shape carries the state, not colour alone - the state is
                  also spelled out in text below for assistive tech. */}
              <span
                aria-hidden="true"
                className="text-xs"
                style={{
                  color:
                    state === 'passed' ? 'var(--status-good)' : 'var(--text-secondary)',
                }}
              >
                {STATE_GLYPH[state]}
              </span>
              <span
                aria-hidden="true"
                className="hidden h-px flex-1 sm:block"
                style={{ background: 'var(--text-secondary)', opacity: 0.3 }}
              />
            </span>

            <span className="flex flex-col">
              <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                {label}
              </span>
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {date ? formatDate(date) : 'Not announced'}
              </span>
              <span className="sr-only">{STATE_LABEL[state]}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}
