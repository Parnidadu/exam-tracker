import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders the base layout shell', () => {
    render(<App />)
    expect(screen.getAllByText('Exam Tracker').length).toBeGreaterThan(0)
  })
})
