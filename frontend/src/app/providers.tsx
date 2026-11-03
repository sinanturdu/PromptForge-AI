import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { createAppQueryClient } from './queryClient'

interface AppProvidersProps {
  readonly children: ReactNode
  readonly queryClient?: QueryClient
}

export function AppProviders({ children, queryClient }: AppProvidersProps) {
  const [defaultClient] = useState(() => createAppQueryClient())
  const activeClient = queryClient ?? defaultClient

  return <QueryClientProvider client={activeClient}>{children}</QueryClientProvider>
}
