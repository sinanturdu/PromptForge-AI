import type { Plugin } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'

export function blockWorkflowProxy(request: IncomingMessage, response: ServerResponse, next: () => void) {
  let path: string
  try {
    path = decodeURIComponent(new URL(request.url ?? '/', 'http://vite.invalid').pathname)
  } catch {
    response.writeHead(400).end()
    return
  }
  if (/^\/api\/v1\/(?:runs(?:\/|$)|prompt-requests\/[^/]+\/runs(?:\/|$))/.test(path)) {
    response.writeHead(403, {
      'Content-Type': 'application/problem+json',
      'Cache-Control': 'no-store',
    }).end(JSON.stringify({
      type: 'about:blank', title: 'Forbidden', status: 403,
      detail: 'Use the built application through verified local backend startup.',
      instance: path, error_code: 'workflow_development_proxy_denied',
    }))
    return
  }
  next()
}

// Vite is for the existing PromptRequest UI, not a workflow execution transport.
export function workflowProxyGuard(): Plugin {
  return {
    name: 'block-workflow-development-proxy',
    configureServer(server) {
      server.middlewares.use(blockWorkflowProxy)
    },
  }
}
