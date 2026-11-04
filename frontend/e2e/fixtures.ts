import { test as base, expect } from '@playwright/test'
import startIsolatedApp from './global-setup'

// Reuse M4C-2A's lifecycle without resetting or sharing a database between tests.
export const test = base.extend<{ isolatedApp: void }>({
  isolatedApp: [async ({ baseURL }, provide) => {
    expect(baseURL).toBe('http://127.0.0.1:15173')
    const cleanup = await startIsolatedApp()
    try {
      await provide()
    } finally {
      await cleanup()
    }
  }, { auto: true }],
})

export { expect }
