import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ExamStageDetail } from '../api/types'
import { StageTimeline } from './StageTimeline'

const TODAY = new Date('2026-06-15T00:00:00Z')

function stage(over: Partial<ExamStageDetail> = {}): ExamStageDetail {
  return {
    id: 10,
    stage_type: 'prelims',
    sequence: 1,
    planned_start_date: null,
    planned_end_date: null,
    notification_date: '2026-03-01',
    admit_card_date: '2026-05-20',
    exam_date: '2026-06-01',
    answer_key_date: '2026-07-01',
    result_date: '2026-08-01',
    status_tracks: [],
    ...over,
  }
}

function renderTimeline(over: Partial<ExamStageDetail> = {}) {
  render(<StageTimeline stage={stage(over)} today={TODAY} />)
  return screen.getByTestId('timeline-10')
}

describe('StageTimeline', () => {
  it('shows all five milestones for the stage', () => {
    const timeline = renderTimeline()
    const items = within(timeline).getAllByRole('listitem')

    expect(items).toHaveLength(5)
    for (const label of [
      'Notification',
      'Admit card',
      'Exam date',
      'Answer key',
      'Result',
    ]) {
      expect(within(timeline).getByText(label)).toBeInTheDocument()
    }
  })

  it('lists the milestones in the order a candidate meets them', () => {
    const timeline = renderTimeline()
    const items = within(timeline).getAllByRole('listitem')

    expect(items.map((li) => li.getAttribute('data-testid'))).toEqual([
      'milestone-10-notification_date',
      'milestone-10-admit_card_date',
      'milestone-10-exam_date',
      'milestone-10-answer_key_date',
      'milestone-10-result_date',
    ])
  })

  it('shows each milestone date', () => {
    const timeline = renderTimeline()
    expect(within(timeline).getByText('1 Mar 2026')).toBeInTheDocument()
    expect(within(timeline).getByText('1 Aug 2026')).toBeInTheDocument()
  })

  it('marks dates in the past as passed and future ones as upcoming', () => {
    renderTimeline()
    expect(screen.getByTestId('milestone-10-exam_date')).toHaveAttribute(
      'data-state',
      'passed',
    )
    expect(screen.getByTestId('milestone-10-answer_key_date')).toHaveAttribute(
      'data-state',
      'upcoming',
    )
  })

  it('counts a milestone dated today as reached', () => {
    renderTimeline({ exam_date: '2026-06-15' })
    expect(screen.getByTestId('milestone-10-exam_date')).toHaveAttribute(
      'data-state',
      'passed',
    )
  })

  it('still shows a milestone whose date is unknown, rather than hiding it', () => {
    // Hiding it would make the timeline silently shorter for some exams and
    // leave a candidate unsure whether the step exists at all.
    renderTimeline({ answer_key_date: null })
    const milestone = screen.getByTestId('milestone-10-answer_key_date')

    expect(milestone).toHaveAttribute('data-state', 'unannounced')
    expect(within(milestone).getByText('Answer key')).toBeInTheDocument()
    expect(within(milestone).getByText('Not announced')).toBeInTheDocument()
  })

  it('renders a full timeline even when no dates are known at all', () => {
    const timeline = renderTimeline({
      notification_date: null,
      admit_card_date: null,
      exam_date: null,
      answer_key_date: null,
      result_date: null,
    })
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(5)
    expect(within(timeline).getAllByText('Not announced')).toHaveLength(5)
  })

  it('states each milestone’s status in text, not by colour alone', () => {
    renderTimeline()
    const passed = screen.getByTestId('milestone-10-exam_date')
    const upcoming = screen.getByTestId('milestone-10-answer_key_date')

    expect(within(passed).getByText('passed')).toBeInTheDocument()
    expect(within(upcoming).getByText('upcoming')).toBeInTheDocument()
  })
})
