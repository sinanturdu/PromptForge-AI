import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
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

const requestFields = new Set(['raw_prompt', 'user_context', 'output_language', 'target_mode', 'task_profile',
  'prompt_detail_level', 'response_detail_preference', 'reasoning_preference'])
const uuidPattern = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

function safeFollowupError(error: unknown): ApiClientError {
  if (!(error instanceof ApiClientError)) {
    return new ApiClientError({ status: 500, errorCode: 'save_failed', userMessage: 'Kayıt yanıtı doğrulanamadı.' })
  }
  // Keep localized field messages, discarding unrecognized server labels and identifiers.
  return new ApiClientError({ status: error.status, errorCode: 'save_failed', userMessage: error.userMessage,
    requestId: error.requestId && uuidPattern.test(error.requestId) ? error.requestId : null,
    validationIssues: error.validationIssues.filter(issue => requestFields.has(issue.field)).map(issue => ({
      ...issue, code: 'validation_error',
    })) })
}

export function useCreatePromptRequestMutation(sourceId?: string) {
  const queryClient = useQueryClient()
  const pending = useRef<{ payload: PromptRequestCreatePayload; allowed: () => boolean } | null>(null)

  const mutation = useMutation<string, ApiClientError, void>({
    mutationFn: async () => {
      const submission = pending.current
      pending.current = null
      if (!submission?.allowed()) {
        throw new ApiClientError({ status: 400, errorCode: 'save_unavailable',
          userMessage: 'Bu taslak artık kaydedilemiyor. Doğrulanmış sonucu yeniden açın.' })
      }
      try {
        const { id } = await createPromptRequest(submission.payload, Boolean(sourceId))
        if (sourceId && (typeof id !== 'string' || !uuidPattern.test(id) ||
          id.toLowerCase() === sourceId.toLowerCase())) {
          throw new ApiClientError({ status: 500, errorCode: 'invalid_created_id',
            userMessage: 'Yeni kayıt kimliği doğrulanamadı.' })
        }
        return id
      } catch (error) {
        throw sourceId ? safeFollowupError(error) : error
      }
    },
    // Draft data stays with the form until dispatch. Mutation records contain
    // neither request variables nor the saved record's private text.
    gcTime: 0,
    networkMode: sourceId ? 'always' : 'online',
    // Non-idempotent POST requests must never be retried automatically.
    retry: false,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: promptRequestKeys.lists() })
    },
  })
  return {
    isPending: mutation.isPending,
    async save(payload: PromptRequestCreatePayload, allowed: () => boolean) {
      pending.current = { payload, allowed }
      try { return await mutation.mutateAsync() }
      finally { pending.current = null }
    },
  }
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
