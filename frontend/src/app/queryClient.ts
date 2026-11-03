import { QueryClient } from '@tanstack/react-query'

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        staleTime: 0,
      },
      mutations: {
        // Non-idempotent mutations must never retry automatically.
        retry: false,
      },
    },
  })
}
