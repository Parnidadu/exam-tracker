import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Exams', end: true },
  { to: '/calendar', label: 'Calendar', end: false },
]

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      {/*
        Skip link: without it a keyboard user re-tabs through the whole nav
        on every page. Visually hidden until focused, so it costs nothing
        for mouse users.
      */}
      <a
        href="#main"
        className="sr-only rounded px-3 py-2 focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50"
        style={{ background: 'var(--surface-1)', color: 'var(--text-primary)' }}
      >
        Skip to main content
      </a>

      <header className="border-b border-gray-200 px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
          <h1 className="text-xl font-semibold" style={{ color: 'var(--text-primary)' }}>
            Exam Tracker
          </h1>
          <nav aria-label="Main">
            <ul className="flex gap-4 text-sm">
              {NAV.map(({ to, label, end }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={end}
                    // aria-current tells assistive tech which page you're on;
                    // the underline is the matching visual cue.
                    className={({ isActive }) => (isActive ? 'underline' : '')}
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>

      <main id="main" className="flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>

      <footer
        className="border-t border-gray-200 px-4 py-4 text-sm sm:px-6"
        style={{ color: 'var(--text-secondary)' }}
      >
        Exam Tracker
      </footer>
    </div>
  )
}
