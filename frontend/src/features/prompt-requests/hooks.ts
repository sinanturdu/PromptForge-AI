import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiClientError } from '../../lib/api-client'
import { createPromptRequest, getPromptRequestById, getPromptRequests } from './api'
import {
  DEFAULT_HISTORY_PAGE,
  DEFAULT_HISTORY_PAGE_SIZE,
  MAX_SEARCH_QUERY_LENGTH,
  type PromptHistoryQueryParams,
  type PromptRequestCreatePayload,
  type PromptRequestListResponse,
  type PromptRequestRecord,
  type TargetMode,
} from './types'

export interface NormalizedPromptListKeyParams {
  readonly page: number
  readonly pageSize: number
  readonly q: string | null
  readonly targetMode: TargetMode | null
}

export function normalizePromptListQueryParams(
  params: PromptHistoryQueryParams,
): NormalizedPromptListKeyParams {
  const trimmedQuery = params.q?.trim()
  return {
    page: params.page,
    pageSize: params.pageSize,
    q: trimmedQuery && trimmedQuery.length > 0 ? trimmedQuery : null,
    targetMode: params.targetMode ?? null,
  }
}

export const promptRequestKeys = {
  all: ['prompt-requests'] as const,
  lists: () => ['prompt-requests', 'list'] as const,
  list: (params: PromptHistoryQueryParams) =>
    ['prompt-requests', 'list', normalizePromptListQueryParams(params)] as const,
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
  params: PromptHistoryQueryParams = {
    page: DEFAULT_HISTORY_PAGE,
    pageSize: DEFAULT_HISTORY_PAGE_SIZE,
  },
) {
  const normalized = normalizePromptListQueryParams(params)
  const isValidRange =
    Number.isInteger(normalized.page) &&
    normalized.page >= 1 &&
    Number.isInteger(normalized.pageSize) &&
    normalized.pageSize >= 1 &&
    (normalized.q === null || normalized.q.length <= MAX_SEARCH_QUERY_LENGTH)

  return useQuery<PromptRequestListResponse, ApiClientError>({
    queryKey: promptRequestKeys.list(params),
    queryFn: ({ signal }) =>
      getPromptRequests(
        {
          page: normalized.page,
          pageSize: normalized.pageSize,
          q: normalized.q ?? undefined,
          targetMode: normalized.targetMode ?? undefined,
        },
        signal,
      ),
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
