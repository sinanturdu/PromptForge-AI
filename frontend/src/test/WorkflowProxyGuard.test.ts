import { expect, it, vi } from 'vitest'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { blockWorkflowProxy } from '../../dev/workflowProxyGuard'

it.each(['/api/v1/runs', '/api/v1/runs/id/execution', '/api/v1/prompt-requests/id/runs',
  '/api/v1/prompt-requests/id/runs?limit=10', '/api/v1/prompt-requests/id%2Fruns'])('blocks Vite forwarding for %s', url => {
  const next = vi.fn()
  const response = { writeHead: vi.fn().mockReturnThis(), end: vi.fn() }
  blockWorkflowProxy({ url } as IncomingMessage, response as unknown as ServerResponse, next)
  expect(next).not.toHaveBeenCalled()
  expect(response.writeHead).toHaveBeenCalledWith(403, expect.objectContaining({ 'Cache-Control': 'no-store' }))
  expect(response.end).toHaveBeenCalledOnce()
})
it.each(['/api/v1/health', '/api/v1/prompt-requests', '/api/v1/prompt-requests/id', '/history'])('preserves existing development path %s', url => {
  const next = vi.fn()
  blockWorkflowProxy({ url } as IncomingMessage, {} as ServerResponse, next)
  expect(next).toHaveBeenCalledOnce()
})
