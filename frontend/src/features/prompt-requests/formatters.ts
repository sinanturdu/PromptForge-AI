import {
  DEFAULT_HISTORY_PAGE,
  MAX_SEARCH_QUERY_LENGTH,
  TARGET_AI_FAMILY_OPTIONS,
  type TargetMode,
} from './types'

const POSITIVE_INTEGER_PATTERN = /^[1-9]\d*$/
const MAX_SAFE_HISTORY_PAGE = 1_000_000

export interface ParsedHistoryPageResult {
  readonly page: number
  readonly isInvalidParam: boolean
}

export interface HistoryUrlState {
  readonly page?: number
  readonly q?: string
  readonly targetMode?: TargetMode
}

export interface ParsedHistoryUrlState {
  readonly page: number
  readonly q: string | undefined
  readonly targetMode: TargetMode | undefined
  readonly needsNormalization: boolean
}

export function isValidTargetMode(value: string): value is TargetMode {
  return TARGET_AI_FAMILY_OPTIONS.some((option) => option.value === value)
}

export function parseHistoryPageParam(rawPageParam: string | null): ParsedHistoryPageResult {
  if (rawPageParam === null) {
    return { page: DEFAULT_HISTORY_PAGE, isInvalidParam: false }
  }

  const trimmed = rawPageParam.trim()
  if (!POSITIVE_INTEGER_PATTERN.test(trimmed)) {
    return { page: DEFAULT_HISTORY_PAGE, isInvalidParam: true }
  }

  const parsed = Number(trimmed)
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > MAX_SAFE_HISTORY_PAGE) {
    return { page: DEFAULT_HISTORY_PAGE, isInvalidParam: true }
  }

  return { page: parsed, isInvalidParam: false }
}

export function parseHistorySearchParams(searchParams: URLSearchParams): ParsedHistoryUrlState {
  const rawPage = searchParams.get('page')
  const parsedPage = parseHistoryPageParam(rawPage)
  const hasRedundantPageOne = rawPage !== null && !parsedPage.isInvalidParam && parsedPage.page === 1

  const rawQ = searchParams.get('q')
  let normalizedQ: string | undefined
  let isInvalidQ = false

  if (rawQ !== null) {
    const trimmedQ = rawQ.trim()
    if (trimmedQ.length === 0) {
      normalizedQ = undefined
      isInvalidQ = true
    } else if (trimmedQ.length > MAX_SEARCH_QUERY_LENGTH) {
      const clamped = trimmedQ.slice(0, MAX_SEARCH_QUERY_LENGTH).trim()
      normalizedQ = clamped.length > 0 ? clamped : undefined
      isInvalidQ = true
    } else {
      normalizedQ = trimmedQ
      if (trimmedQ !== rawQ) {
        isInvalidQ = true
      }
    }
  }

  const rawTargetMode = searchParams.get('target_mode')
  let normalizedTargetMode: TargetMode | undefined
  let isInvalidTargetMode = false

  if (rawTargetMode !== null) {
    if (isValidTargetMode(rawTargetMode)) {
      normalizedTargetMode = rawTargetMode
    } else {
      normalizedTargetMode = undefined
      isInvalidTargetMode = true
    }
  }

  return {
    page: parsedPage.page,
    q: normalizedQ,
    targetMode: normalizedTargetMode,
    needsNormalization:
      parsedPage.isInvalidParam || hasRedundantPageOne || isInvalidQ || isInvalidTargetMode,
  }
}

export function buildHistorySearchParams(state: HistoryUrlState): URLSearchParams {
  const params = new URLSearchParams()

  const trimmedQ = state.q?.trim()
  if (trimmedQ && trimmedQ.length > 0) {
    params.set('q', trimmedQ.slice(0, MAX_SEARCH_QUERY_LENGTH))
  }

  if (state.targetMode && isValidTargetMode(state.targetMode)) {
    params.set('target_mode', state.targetMode)
  }

  if (state.page !== undefined && Number.isFinite(state.page) && Math.trunc(state.page) > 1) {
    params.set('page', String(Math.trunc(state.page)))
  }

  return params
}

export function buildHistoryUrl(state: HistoryUrlState, basePath: string = '/history'): string {
  const params = buildHistorySearchParams(state)
  const serialized = params.toString()
  return serialized.length > 0 ? `${basePath}?${serialized}` : basePath
}

export function calculateTotalPages(total: number, pageSize: number): number {
  if (total <= 0 || pageSize <= 0) {
    return 0
  }
  return Math.ceil(total / pageSize)
}

export function formatPromptTimestamp(isoTimestamp: string): string {
  const parsed = new Date(isoTimestamp)
  if (Number.isNaN(parsed.getTime())) {
    return isoTimestamp
  }
  return new Intl.DateTimeFormat('tr-TR', {
    dateStyle: 'long',
    timeStyle: 'medium',
  }).format(parsed)
}
