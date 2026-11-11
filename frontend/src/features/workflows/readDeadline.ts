import { WorkflowClientError } from './errors'

export const WORKFLOW_READ_DEADLINE_MS = 10_000

// Per-read deadline, not a polling scheduler. Preserve navigation cancellation;
// a stalled read is a network problem, never a persisted workflow failure.
export async function workflowRead<T>(signal: AbortSignal, read: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  let expired = false
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
  const timer = setTimeout(() => { expired = true; controller.abort() }, WORKFLOW_READ_DEADLINE_MS)
  try { return await read(controller.signal) }
  catch (error) {
    if (expired && !signal.aborted) throw new WorkflowClientError('network')
    throw error
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', cancel)
  }
}
