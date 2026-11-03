import { DEFAULT_HISTORY_PAGE } from './types'

const POSITIVE_INTEGER_PATTERN = /^[1-9]\d*$/
const MAX_SAFE_HISTORY_PAGE = 1_000_000

export interface ParsedHistoryPageResult {
  readonly page: number
  readonly isInvalidParam: boolean
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
