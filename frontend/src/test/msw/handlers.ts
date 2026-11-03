import { HttpResponse, http } from 'msw'
import type {
  PromptRequestCreatePayload,
  PromptRequestRecord,
} from '../../features/prompt-requests/types'

const mockPromptStore = new Map<string, PromptRequestRecord>()
let recordedPostPayloads: PromptRequestCreatePayload[] = []
let recordedGetIds: string[] = []
let sequenceCounter = 1

export function resetMockPromptStore(): void {
  mockPromptStore.clear()
  recordedPostPayloads = []
  recordedGetIds = []
  sequenceCounter = 1
}

export function seedMockPromptRecord(record: PromptRequestRecord): void {
  mockPromptStore.set(record.id, record)
}

export function getRecordedPostPayloads(): readonly PromptRequestCreatePayload[] {
  return recordedPostPayloads
}

export function getRecordedGetIds(): readonly string[] {
  return recordedGetIds
}

export const defaultHandlers = [
  http.post('/api/v1/prompt-requests', async ({ request }) => {
    const body = (await request.json()) as PromptRequestCreatePayload
    recordedPostPayloads.push(body)

    const generatedId = `550e8400-e29b-41d4-a716-44665544000${sequenceCounter}`
    const requestId = `req-post-000${sequenceCounter}`
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
      created_at: '2026-11-03T10:30:00Z',
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
