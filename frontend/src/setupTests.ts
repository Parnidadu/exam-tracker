import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Testing Library only auto-registers its cleanup when Vitest runs with
// `globals: true`. This project uses explicit imports instead, so without
// this every rendered component stays mounted and leaks into later tests
// (duplicate elements, stale key handlers).
afterEach(cleanup)
