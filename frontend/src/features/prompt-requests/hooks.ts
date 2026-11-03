import { useMutation, useQuery } from '@tanstack/react-query'
import { ApiClientError } from '../../lib/api-client'
import { createPromptRequest, getPromptRequestById } from './api'
import type { PromptRequestCreatePayload, PromptRequestRecord } from './types'

export const promptRequestKeys = {
  all: ['prompt-requests'] as const,
  detail: (id: string) => ['prompt-requests', 'detail', id] as const,
}

export function useCreatePromptRequestMutation() {
  return useMutation<PromptRequestRecord, ApiClientError, PromptRequestCreatePayload>({
    mutationFn: createPromptRequest,
    // Non-idempotent POST requests must never be retried automatically.
    retry: false,
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
    retry: (failureCount, error) => {
      if (error.status === 400 || error.status === 404 || error.status === 422) {
        return false
      }
      return failureCount < 1
    },
  })
}
