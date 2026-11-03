import {
  buildHistoryUrl,
  type HistoryUrlState,
} from '../features/prompt-requests/formatters'

export const ROUTES = {
  newPrompt: '/',
  promptHistory: '/history',
  promptHistoryWithPage: (page: number) => buildHistoryUrl({ page }, '/history'),
  promptHistoryWithState: (state: HistoryUrlState) => buildHistoryUrl(state, '/history'),
  promptRequestDetailPattern: '/prompt-requests/:id',
  promptRequestDetail: (id: string) => `/prompt-requests/${encodeURIComponent(id)}`,
} as const
