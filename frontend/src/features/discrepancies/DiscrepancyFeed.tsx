import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { fetchPublicDiscrepancies } from '../../api/client'
import type { PublicDiscrepancy } from '../../api/types'

/**
 * The public discrepancy feed (EXT-055).
 *
 * Everything reaching this component has already been checked by a
 * verifier: the endpoint only returns confirmed and resolved ones, so
 * there is no filtering to do here. That is on purpose - a visibility
 * rule enforced in the UI is a visibility rule one careless render away
 * from being broken, and this component is not where it lives.
 */
const MAX_ON_DASHBOARD = 5

export function DiscrepancyFeed() {
  const [items, setItems] = useState<PublicDiscrepancy[]>([])
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    fetchPublicDiscrepancies()
      .then((page) => {
        if (active) setItems(page.results.slice(0, MAX_ON_DASHBOARD))
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
    }
  }, [])

  // Nothing wrong anywhere is the normal state, and an empty panel saying
  // so would be permanent furniture on the busiest page in the app.
  if (failed || !items.length) return null

  return (
    <section aria-labelledby="discrepancy-feed-heading" className="mb-6 rounded border border-gray-200 p-4">
      <h3 id="discrepancy-feed-heading" className="mb-3 font-semibold">
        Recent discrepancies
      </h3>

      <ul className="space-y-3">
        {items.map((item) => (
          <li key={item.id}>
            <p className="text-sm">
              <strong>{item.type_label}</strong>
              {' — '}
              <Link to={`/exams/${item.exam_slug}`}>
                {item.board_code} {item.exam_name}
              </Link>{' '}
              ({item.stage_type})
              {/* Said in words, not colour alone: "resolved" and "still
                  open" is the distinction a candidate is actually here
                  for, and a coloured dot does not carry it. */}
              <span className="ml-2">
                {item.status === 'resolved' ? 'Resolved' : 'Confirmed, ongoing'}
              </span>
            </p>

            <p className="text-sm">{item.description}</p>

            {item.resolution_note && <p className="text-sm">{item.resolution_note}</p>}

            <p className="text-sm">
              <a href={item.evidence_url} target="_blank" rel="noopener noreferrer">
                Official notice
              </a>
              {item.occurred_on && <> · {item.occurred_on}</>}
            </p>
          </li>
        ))}
      </ul>
    </section>
  )
}
