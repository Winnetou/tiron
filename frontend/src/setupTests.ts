import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Vitest has no globals here, so Testing Library can't register this itself.
afterEach(cleanup)
