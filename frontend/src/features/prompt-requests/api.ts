import { apiRequest } from '../../lib/api-client'
import {
  DEFAULT_HISTORY_PAGE,
  DEFAULT_HISTORY_PAGE_SIZE,
  type PromptHistoryQueryParams,
  type PromptRequestCreatePayload,
  type PromptRequestListResponse,
  type PromptRequestRecord,
} from './types'

export async function createPromptRequest(
  payload: PromptRequestCreatePayload,
): Promise<PromptRequestRecord> {
  return apiRequest<PromptRequestRecord>('/prompt-requests', {
    method: 'POST',
    jsonBody: payload,
  })
}

export async function getPromptRequests(
  params: PromptHistoryQueryParams = {
    page: DEFAULT_HISTORY_PAGE,
    pageSize: DEFAULT_HISTORY_PAGE_SIZE,
  },
  signal?: AbortSignal,
): Promise<PromptRequestListResponse> {
  const searchParams = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  })

  const normalizedQuery = params.q?.trim()
  if (normalizedQuery && normalizedQuery.length > 0) {
    searchParams.set('q', normalizedQuery)
  }

  if (params.targetMode) {
    searchParams.set('target_mode', params.targetMode)
  }

  return apiRequest<PromptRequestListResponse>(`/prompt-requests?${searchParams.toString()}`, {
    method: 'GET',
    signal,
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
