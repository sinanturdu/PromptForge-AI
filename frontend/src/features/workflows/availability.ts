import { WorkflowClientError } from './errors'

// Observations, not advertised capabilities. Nothing here probes or starts work.
export type WorkflowAvailability = 'unknown' | 'api_observed_execution_unknown' |
  'creation_observed_execution_unknown' | 'execution_accepted' | 'execution_unavailable' |
  'access_denied' | 'route_unavailable'
export function observeWorkflowOutcome(
  operation: 'read' | 'create' | 'start', outcome: 'success' | WorkflowClientError,
): WorkflowAvailability {
  if (outcome === 'success') return operation === 'start' ? 'execution_accepted'
    : operation === 'create' ? 'creation_observed_execution_unknown' : 'api_observed_execution_unknown'
  if (outcome.kind !== 'api') return 'unknown'
  if (outcome.status === 403) return 'access_denied'
  if (outcome.status === 503) return 'execution_unavailable'
  if (outcome.errorCode === 'not_found' || outcome.errorCode === 'method_not_allowed') return 'route_unavailable'
  // A resource 404 or conflict/capacity response proves API contact, not readiness.
  return 'api_observed_execution_unknown'
}
