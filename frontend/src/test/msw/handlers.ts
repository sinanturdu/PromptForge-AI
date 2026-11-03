import { HttpResponse, http } from 'msw'
import { isValidTargetMode } from '../../features/prompt-requests/formatters'
import {
  MAX_SEARCH_QUERY_LENGTH,
  type PromptRequestCreatePayload,
  type PromptRequestListResponse,
  type PromptRequestRecord,
} from '../../features/prompt-requests/types'

export interface RecordedListQuery {
  readonly rawPage: string | null
  readonly rawPageSize: string | null
  readonly page: number
  readonly pageSize: number
  readonly rawQ?: string
  readonly rawTargetMode?: string
}

const mockPromptStore = new Map<string, PromptRequestRecord>()
let recordedPostPayloads: PromptRequestCreatePayload[] = []
let recordedGetIds: string[] = []
let recordedListQueries: RecordedListQuery[] = []
let sequenceCounter = 1

export function resetMockPromptStore(): void {
  mockPromptStore.clear()
  recordedPostPayloads = []
  recordedGetIds = []
  recordedListQueries = []
  sequenceCounter = 1
}

export function seedMockPromptRecord(record: PromptRequestRecord): void {
  mockPromptStore.set(record.id, record)
}

export function seedMockPromptRecords(records: readonly PromptRequestRecord[]): void {
  for (const record of records) {
    mockPromptStore.set(record.id, record)
  }
}

export function getRecordedPostPayloads(): readonly PromptRequestCreatePayload[] {
  return recordedPostPayloads
}

export function getRecordedGetIds(): readonly string[] {
  return recordedGetIds
}

export function getRecordedListQueries(): readonly RecordedListQuery[] {
  return recordedListQueries
}

function sortRecordsNewestFirst(
  records: readonly PromptRequestRecord[],
): PromptRequestRecord[] {
  return [...records].sort((left, right) => {
    const timeDiff = Date.parse(right.created_at) - Date.parse(left.created_at)
    if (timeDiff !== 0) {
      return timeDiff
    }
    return right.id.localeCompare(left.id)
  })
}

function matchesSearchAndTargetFilters(
  record: PromptRequestRecord,
  normalizedQuery: string,
  targetMode: string | null,
): boolean {
  if (targetMode !== null && record.target_mode !== targetMode) {
    return false
  }

  if (normalizedQuery.length === 0) {
    return true
  }

  const needle = normalizedQuery.toLowerCase()
  const matchesRawPrompt = record.raw_prompt.toLowerCase().includes(needle)
  const matchesUserContext =
    record.user_context !== null && record.user_context.toLowerCase().includes(needle)

  return matchesRawPrompt || matchesUserContext
}

export const defaultHandlers = [
  http.post('/api/v1/prompt-requests', async ({ request }) => {
    const body = (await request.json()) as PromptRequestCreatePayload
    recordedPostPayloads.push(body)

    const generatedId = `550e8400-e29b-41d4-a716-44665544000${sequenceCounter}`
    const requestId = `req-post-000${sequenceCounter}`
    const createdTimestamp = `2026-11-03T18:30:0${sequenceCounter}Z`
    sequenceCounter += 1

    const savedRecord: PromptRequestRecord = {
      id: generatedId,
      raw_prompt: body.raw_prompt,
      user_context: body.user_context,
      output_language: body.output_language ?? 'tr',
      target_mode: body.target_mode,
      task_profile: body.task_profile,
      prompt_detail_level: body.prompt_detail_level,
      response_detail_preference: body.response_detail_preference,
      reasoning_preference: body.reasoning_preference,
      created_at: createdTimestamp,
    }
    mockPromptStore.set(savedRecord.id, savedRecord)

    return HttpResponse.json(savedRecord, {
      status: 201,
      headers: {
        Location: `/api/v1/prompt-requests/${savedRecord.id}`,
        'X-Request-ID': requestId,
      },
    })
  }),

  http.get('/api/v1/prompt-requests', ({ request }) => {
    const url = new URL(request.url)
    const rawPage = url.searchParams.get('page')
    const rawPageSize = url.searchParams.get('page_size')
    const rawQ = url.searchParams.get('q')
    const rawTargetMode = url.searchParams.get('target_mode')

    const parsedPage = rawPage === null ? 1 : Number(rawPage)
    const parsedPageSize = rawPageSize === null ? 20 : Number(rawPageSize)

    recordedListQueries.push({
      rawPage,
      rawPageSize,
      page: parsedPage,
      pageSize: parsedPageSize,
      ...(rawQ !== null ? { rawQ } : {}),
      ...(rawTargetMode !== null ? { rawTargetMode } : {}),
    })

    if (
      !Number.isInteger(parsedPage) ||
      parsedPage < 1 ||
      !Number.isInteger(parsedPageSize) ||
      parsedPageSize < 1 ||
      parsedPageSize > 100
    ) {
      return HttpResponse.json(
        {
          type: 'about:blank',
          title: 'Unprocessable Content',
          status: 422,
          detail: 'Request validation failed.',
          instance: '/api/v1/prompt-requests',
          request_id: 'req-422-invalid-pagination',
          error_code: 'validation_error',
        },
        {
          status: 422,
          headers: {
            'Content-Type': 'application/problem+json',
            'X-Request-ID': 'req-422-invalid-pagination',
          },
        },
      )
    }

    if (rawQ !== null && rawQ.length > MAX_SEARCH_QUERY_LENGTH) {
      return HttpResponse.json(
        {
          type: 'about:blank',
          title: 'Unprocessable Content',
          status: 422,
          detail: 'One or more request fields failed validation.',
          instance: '/api/v1/prompt-requests',
          request_id: 'req-422-query-too-long',
          error_code: 'validation_error',
          errors: [
            {
              field: 'q',
              code: 'string_too_long',
              message: `String should have at most ${MAX_SEARCH_QUERY_LENGTH} characters`,
            },
          ],
        },
        {
          status: 422,
          headers: {
            'Content-Type': 'application/problem+json',
            'X-Request-ID': 'req-422-query-too-long',
          },
        },
      )
    }

    if (rawTargetMode !== null && !isValidTargetMode(rawTargetMode)) {
      return HttpResponse.json(
        {
          type: 'about:blank',
          title: 'Unprocessable Content',
          status: 422,
          detail: 'One or more request fields failed validation.',
          instance: '/api/v1/prompt-requests',
          request_id: 'req-422-invalid-target-mode',
          error_code: 'validation_error',
          errors: [
            {
              field: 'target_mode',
              code: 'enum',
              message: "Input should be 'generic', 'openai', 'anthropic' or 'google'",
            },
          ],
        },
        {
          status: 422,
          headers: {
            'Content-Type': 'application/problem+json',
            'X-Request-ID': 'req-422-invalid-target-mode',
          },
        },
      )
    }

    const normalizedQuery = rawQ?.trim() ?? ''
    const matchingRecords = Array.from(mockPromptStore.values()).filter((record) =>
      matchesSearchAndTargetFilters(record, normalizedQuery, rawTargetMode),
    )
    const sortedRecords = sortRecordsNewestFirst(matchingRecords)
    const startIndex = (parsedPage - 1) * parsedPageSize
    const pageItems = sortedRecords.slice(startIndex, startIndex + parsedPageSize)

    const responseBody: PromptRequestListResponse = {
      items: pageItems,
      total: sortedRecords.length,
      page: parsedPage,
      page_size: parsedPageSize,
    }

    return HttpResponse.json(responseBody, {
      status: 200,
      headers: {
        'X-Request-ID': `req-list-p${parsedPage}`,
      },
    })
  }),

  http.get('/api/v1/prompt-requests/:id', ({ params }) => {
    const id = String(params.id ?? '')
    recordedGetIds.push(id)

    const existing = mockPromptStore.get(id)
    if (!existing) {
      return HttpResponse.json(
        {
          type: 'about:blank',
          title: 'Not Found',
          status: 404,
          detail: 'Prompt request not found.',
          instance: `/api/v1/prompt-requests/${id}`,
          request_id: 'req-404-missing-id',
          error_code: 'prompt_request_not_found',
        },
        {
          status: 404,
          headers: {
            'Content-Type': 'application/problem+json',
            'X-Request-ID': 'req-404-missing-id',
          },
        },
      )
    }

    return HttpResponse.json(existing, {
      status: 200,
      headers: {
        'X-Request-ID': `req-get-${id.slice(-4)}`,
      },
    })
  }),
]
