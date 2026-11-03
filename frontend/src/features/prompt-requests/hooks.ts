import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiClientError } from '../../lib/api-client'
import { createPromptRequest, getPromptRequestById, getPromptRequests } from './api'
import {
  DEFAULT_HISTORY_PAGE,
  DEFAULT_HISTORY_PAGE_SIZE,
  type PromptRequestCreatePayload,
  type PromptRequestListResponse,
  type PromptRequestRecord,
} from './types'

export const promptRequestKeys = {
  all: ['prompt-requests'] as const,
  lists: () => ['prompt-requests', 'list'] as const,
  list: (page: number, pageSize: number) =>
    ['prompt-requests', 'list', { page, pageSize }] as const,
  details: () => ['prompt-requests', 'detail'] as const,
  detail: (id: string) => ['prompt-requests', 'detail', id] as const,
}

function shouldRetryReadQuery(failureCount: number, error: ApiClientError): boolean {
  if (error.status === 400 || error.status === 404 || error.status === 422) {
    return false
  }
  return failureCount < 1
}

export function useCreatePromptRequestMutation() {
  const queryClient = useQueryClient()

  return useMutation<PromptRequestRecord, ApiClientError, PromptRequestCreatePayload>({
    mutationFn: createPromptRequest,
    // Non-idempotent POST requests must never be retried automatically.
    retry: false,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: promptRequestKeys.lists() })
    },
  })
}

export function usePromptRequestsQuery(
  page: number = DEFAULT_HISTORY_PAGE,
  pageSize: number = DEFAULT_HISTORY_PAGE_SIZE,
) {
  const isValidRange = Number.isInteger(page) && page >= 1 && Number.isInteger(pageSize) && pageSize >= 1

  return useQuery<PromptRequestListResponse, ApiClientError>({
    queryKey: promptRequestKeys.list(page, pageSize),
    queryFn: ({ signal }) => getPromptRequests(page, pageSize, signal),
    enabled: isValidRange,
    retry: shouldRetryReadQuery,
  })
}

export function usePromptRequestQuery(promptRequestId: string | undefined) {
  return useQuery<PromptRequestRecord, ApiClientError>({
    queryKey: promptRequestKeys.detail(promptRequestId ?? ''),
    queryFn: ({ signal }) => {
      if (!promptRequestId) {
        throw new ApiClientError({
          status: 400,
          errorCode: 'missing_id',
          userMessage: 'Geçerli bir istek kimliği bulunamadı.',
        })
      }
      return getPromptRequestById(promptRequestId, signal)
    },
    enabled: Boolean(promptRequestId),
    retry: shouldRetryReadQuery,
  })
}
