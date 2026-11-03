export const ROUTES = {
  newPrompt: '/',
  promptHistory: '/history',
  promptHistoryWithPage: (page: number) =>
    page <= 1 ? '/history' : `/history?page=${encodeURIComponent(String(page))}`,
  promptRequestDetailPattern: '/prompt-requests/:id',
  promptRequestDetail: (id: string) => `/prompt-requests/${encodeURIComponent(id)}`,
} as const
