export const ROUTES = {
  newPrompt: '/',
  promptRequestDetailPattern: '/prompt-requests/:id',
  promptRequestDetail: (id: string) => `/prompt-requests/${encodeURIComponent(id)}`,
} as const
