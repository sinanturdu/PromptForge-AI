import type { ValidationErrorItem } from '../../lib/api-client'

export interface WorkflowProblemDetails {
  readonly type: string
  readonly title: string
  readonly status: number
  readonly detail: string
  readonly instance: string
  readonly request_id: string
  readonly error_code: string
  readonly errors?: readonly ValidationErrorItem[] | null
}
export type WorkflowErrorKind = 'api' | 'network' | 'cancelled' | 'malformed_response' | 'invalid_request' | 'duplicate_start'
const messages: Readonly<Record<number, string>> = {
  403: 'İş akışı erişimi güvenlik politikası tarafından reddedildi.',
  404: 'İş akışı veya API yolu bulunamadı.',
  409: 'İş akışının mevcut durumu bu işleme uygun değil veya sonuç henüz hazır değil.',
  422: 'Gönderilen iş akışı bilgileri geçersiz.',
  429: 'Başka bir iş akışı çalışıyor. Yeni işlem kuyruğa alınmadı.',
  503: 'Güvenli iş akışı yürütme ortamı şu anda kullanılamıyor.',
  500: 'İş akışı işlenirken sunucu hatası oluştu.',
}
const codes = new Set(['workflow_access_denied', 'workflow_not_found', 'prompt_request_not_found',
  'workflow_conflict', 'workflow_integrity_inconsistent', 'workflow_not_terminal', 'validation_error',
  'workflow_capacity_exceeded', 'workflow_execution_environment_unsafe', 'workflow_runtime_unavailable',
  'workflow_unavailable', 'workflow_execution_disabled', 'not_found', 'method_not_allowed',
  'internal_server_error', 'request_body_too_large', 'workflow_development_proxy_denied'])

export class WorkflowClientError extends Error {
  readonly kind: WorkflowErrorKind
  readonly status: number
  readonly errorCode: string
  readonly requestId: string | null
  constructor(kind: WorkflowErrorKind, status = 0, code: string = kind, requestId: string | null = null) {
    super(kind === 'api' ? messages[status] ?? 'İş akışı isteği reddedildi.' : {
      network: 'Sunucuya bağlanılamadı.', cancelled: 'İstek iptal edildi.',
      malformed_response: 'Sunucudan geçersiz iş akışı yanıtı alındı.',
      invalid_request: 'İş akışı isteği geçersiz.', duplicate_start: 'Bu iş akışı için bir başlatma isteği zaten sürüyor.',
    }[kind])
    this.name = 'WorkflowClientError'
    this.kind = kind
    this.status = status
    this.errorCode = codes.has(code) ? code : kind === 'api' ? `http_${status}` : kind
    this.requestId = requestId && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestId) ? requestId : null
  }
}

// Read only approved diagnostic identities. Never attach body/detail/exception/cause
// to an Error (query/devtools/UI may inspect it). UI messages are fixed locally.
export function apiFailure(status: number, body: unknown, headerId: string | null): WorkflowClientError {
  let code = ''
  let requestId = headerId
  if (typeof body === 'object' && body !== null && !Array.isArray(body)) {
    const row = body as Record<string, unknown>
    if (row.status === status && typeof row.error_code === 'string') {
      code = row.error_code
      if (typeof row.request_id === 'string') requestId = row.request_id
    }
  }
  return new WorkflowClientError('api', status, code, requestId)
}
