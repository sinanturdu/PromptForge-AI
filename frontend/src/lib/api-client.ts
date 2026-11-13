export interface ValidationErrorItem {
  readonly field: string
  readonly code: string
  readonly message: string
}

export interface ProblemDetails {
  readonly type?: string
  readonly title?: string
  readonly status?: number
  readonly detail?: string
  readonly instance?: string
  readonly request_id?: string
  readonly error_code?: string
  readonly errors?: readonly ValidationErrorItem[]
}

export interface LocalizedValidationIssue {
  readonly field: string
  readonly fieldLabel: string
  readonly code: string
  readonly message: string
}

const API_V1_PREFIX = '/api/v1'
const REQUEST_ID_HEADER = 'X-Request-ID'

const FIELD_LABELS_TR: Readonly<Record<string, string>> = {
  raw_prompt: 'Ham Prompt',
  user_context: 'Proje Bağlamı',
  output_language: 'Çıktı Dili',
  target_mode: 'Hedef Yapay Zekâ Ailesi',
  task_profile: 'Görev Profili',
  prompt_detail_level: 'Prompt Detay Seviyesi',
  response_detail_preference: 'Yanıt Detay Tercihi',
  reasoning_preference: 'Akıl Yürütme Tercihi',
  id: 'İstek Kimliği (UUID)',
  q: 'Arama Sorgusu',
  page: 'Sayfa Numarası',
  page_size: 'Sayfa Boyutu',
}

export function resolveApiBaseUrl(
  configuredValue: string | undefined = import.meta.env.VITE_API_BASE_URL,
): string {
  if (!configuredValue) {
    return ''
  }
  return configuredValue.trim().replace(/\/+$/, '')
}

export function buildApiV1Url(path: string, baseUrl: string = resolveApiBaseUrl()): string {
  const normalizedBase = resolveApiBaseUrl(baseUrl)
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  if (!normalizedBase) {
    return `${API_V1_PREFIX}${normalizedPath}`
  }
  if (normalizedBase.endsWith(API_V1_PREFIX)) {
    return `${normalizedBase}${normalizedPath}`
  }
  return `${normalizedBase}${API_V1_PREFIX}${normalizedPath}`
}

export function localizeValidationIssue(item: ValidationErrorItem): LocalizedValidationIssue {
  const fieldLabel = FIELD_LABELS_TR[item.field] ?? item.field

  switch (item.code) {
    case 'missing':
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `${fieldLabel} alanı zorunludur.`,
      }
    case 'string_too_short':
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `${fieldLabel} alanı boş bırakılamaz.`,
      }
    case 'string_too_long':
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `${fieldLabel} izin verilen azami karakter sınırını aşıyor.`,
      }
    case 'value_error':
      if (item.field === 'raw_prompt') {
        return {
          field: item.field,
          fieldLabel,
          code: item.code,
          message: 'Ham prompt boş veya yalnızca boşluk karakterlerinden oluşamaz.',
        }
      }
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `${fieldLabel} alanı için geçersiz bir değer gönderildi.`,
      }
    case 'enum':
    case 'literal_error':
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `${fieldLabel} için desteklenmeyen bir seçenek gönderildi.`,
      }
    case 'uuid_parsing':
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: 'Geçerli bir istek UUID değeri belirtilmelidir.',
      }
    case 'extra_forbidden':
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `Beklenmeyen alan gönderildi: ${fieldLabel}.`,
      }
    default:
      return {
        field: item.field,
        fieldLabel,
        code: item.code,
        message: `${fieldLabel} alanı doğrulanamadı.`,
      }
  }
}

function mapStatusToSafeTurkishMessage(status: number, errorCode: string): string {
  if (status === 422 || errorCode === 'validation_error' || errorCode === 'domain_validation_error') {
    return 'Gönderilen bilgilerde doğrulama hatası bulundu. Lütfen işaretli alanları kontrol edin.'
  }
  if (status === 413 || errorCode === 'request_body_too_large') {
    return 'Gönderilen veri boyutu izin verilen azami sınırı aşıyor. Lütfen metni kısaltıp tekrar deneyin.'
  }
  if (status === 404 || errorCode === 'prompt_request_not_found' || errorCode === 'not_found') {
    return 'Aradığınız prompt isteği bulunamadı. Bağlantı hatalı veya kayıt mevcut olmayabilir.'
  }
  if (status === 400) {
    return 'İstek biçimi geçersiz olduğu için sunucu tarafından işlenemedi.'
  }
  if (status >= 500) {
    return 'Sunucu tarafında beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin.'
  }
  return 'İstek işlenirken beklenmeyen bir hata oluştu. Lütfen tekrar deneyin.'
}

export class ApiClientError extends Error {
  readonly status: number
  readonly errorCode: string
  readonly userMessage: string
  readonly requestId: string | null
  readonly validationIssues: readonly LocalizedValidationIssue[]
  readonly isNetworkError: boolean

  constructor(params: {
    status: number
    errorCode: string
    userMessage: string
    requestId?: string | null
    validationIssues?: readonly LocalizedValidationIssue[]
    isNetworkError?: boolean
  }) {
    super(params.userMessage)
    this.name = 'ApiClientError'
    this.status = params.status
    this.errorCode = params.errorCode
    this.userMessage = params.userMessage
    this.requestId = params.requestId ?? null
    this.validationIssues = params.validationIssues ?? []
    this.isNetworkError = params.isNetworkError ?? false
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseProblemDetails(payload: unknown): ProblemDetails | null {
  if (!isRecord(payload)) {
    return null
  }

  const rawErrors = payload.errors
  const parsedErrors: ValidationErrorItem[] = []
  if (Array.isArray(rawErrors)) {
    for (const entry of rawErrors) {
      if (
        isRecord(entry) &&
        typeof entry.field === 'string' &&
        typeof entry.code === 'string' &&
        typeof entry.message === 'string'
      ) {
        parsedErrors.push({
          field: entry.field,
          code: entry.code,
          message: entry.message,
        })
      }
    }
  }

  return {
    type: typeof payload.type === 'string' ? payload.type : undefined,
    title: typeof payload.title === 'string' ? payload.title : undefined,
    status: typeof payload.status === 'number' ? payload.status : undefined,
    detail: typeof payload.detail === 'string' ? payload.detail : undefined,
    instance: typeof payload.instance === 'string' ? payload.instance : undefined,
    request_id: typeof payload.request_id === 'string' ? payload.request_id : undefined,
    error_code: typeof payload.error_code === 'string' ? payload.error_code : undefined,
    errors: parsedErrors.length > 0 ? parsedErrors : undefined,
  }
}

export interface ApiRequestOptions {
  readonly method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  readonly jsonBody?: unknown
  readonly signal?: AbortSignal
  readonly headers?: Readonly<Record<string, string>>
  readonly sameOrigin?: boolean
}

export async function apiRequest<TResponse>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<TResponse> {
  const url = options.sameOrigin ? buildApiV1Url(path, '') : buildApiV1Url(path)
  const method = options.method ?? 'GET'
  if (options.sameOrigin && new URL(url, document.baseURI).origin !== window.location.origin) {
    throw new ApiClientError({ status: 400, errorCode: 'invalid_request_origin',
      userMessage: 'Kayıt için aynı uygulama adresi kullanılmalıdır.' })
  }

  const requestHeaders: Record<string, string> = {
    Accept: 'application/json, application/problem+json',
    ...options.headers,
  }
  if (options.jsonBody !== undefined) {
    requestHeaders['Content-Type'] = 'application/json'
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      ...(options.sameOrigin ? { mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store' } : {}),
      headers: requestHeaders,
      body: options.jsonBody !== undefined ? JSON.stringify(options.jsonBody) : undefined,
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    throw new ApiClientError({
      status: 0,
      errorCode: 'network_error',
      userMessage:
        'Backend servisine bağlanılamadı. Sunucunun çalıştığından emin olup tekrar deneyin.',
      isNetworkError: true,
    })
  }

  const headerRequestId = response.headers.get(REQUEST_ID_HEADER)

  if (!response.ok) {
    let parsedProblem: ProblemDetails | null = null
    const contentType = response.headers.get('content-type') ?? ''

    if (contentType.includes('application/problem+json') || contentType.includes('application/json')) {
      try {
        const errorJson: unknown = await response.json()
        parsedProblem = parseProblemDetails(errorJson)
      } catch {
        parsedProblem = null
      }
    }

    const errorCode = parsedProblem?.error_code ?? `http_${response.status}`
    const resolvedRequestId = parsedProblem?.request_id ?? headerRequestId ?? null
    const validationIssues = parsedProblem?.errors
      ? parsedProblem.errors.map(localizeValidationIssue)
      : []

    throw new ApiClientError({
      status: response.status,
      errorCode,
      userMessage: mapStatusToSafeTurkishMessage(response.status, errorCode),
      requestId: resolvedRequestId,
      validationIssues,
    })
  }

  try {
    return (await response.json()) as TResponse
  } catch {
    throw new ApiClientError({
      status: response.status,
      errorCode: 'invalid_response_format',
      userMessage: 'Sunucudan beklenmeyen bir yanıt biçimi alındı.',
      requestId: headerRequestId,
    })
  }
}
