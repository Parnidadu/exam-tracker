import type { ReactNode } from 'react'

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-gray-200 px-6 py-4">
        <h1 className="text-xl font-semibold text-gray-900">Exam Tracker</h1>
      </header>
      <main className="flex-1 px-6 py-8">{children}</main>
      <footer className="border-t border-gray-200 px-6 py-4 text-sm text-gray-500">
        Exam Tracker
      </footer>
    </div>
  )
}
