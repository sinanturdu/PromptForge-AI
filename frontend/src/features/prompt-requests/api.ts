import { apiRequest } from '../../lib/api-client'
import type { PromptRequestCreatePayload, PromptRequestRecord } from './types'

export async function createPromptRequest(
  payload: PromptRequestCreatePayload,
): Promise<PromptRequestRecord> {
  return apiRequest<PromptRequestRecord>('/prompt-requests', {
    method: 'POST',
    jsonBody: payload,
  })
}

export async function getPromptRequestById(
  promptRequestId: string,
  signal?: AbortSignal,
): Promise<PromptRequestRecord> {
  const encodedId = encodeURIComponent(promptRequestId)
  return apiRequest<PromptRequestRecord>(`/prompt-requests/${encodedId}`, {
    method: 'GET',
    signal,
  })
}
