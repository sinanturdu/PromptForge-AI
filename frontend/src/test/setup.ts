import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { resetMockPromptStore } from './msw/handlers'
import { server } from './msw/server'

beforeAll(() => {
  server.listen({ onUnhandledFrame: 'error' })
})

afterEach(() => {
  server.resetHandlers()
  resetMockPromptStore()
})

afterAll(() => {
  server.close()
})
