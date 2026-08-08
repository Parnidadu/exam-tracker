import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App', () => {
  it('renders the base layout shell', () => {
    renderAt('/')
    expect(screen.getAllByText('Exam Tracker').length).toBeGreaterThan(0)
  })

  it('routes /verify to the verifier console', () => {
    renderAt('/verify')
    expect(screen.getByText('Loading queue…')).toBeInTheDocument()
  })
})
