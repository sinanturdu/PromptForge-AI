import { QueryClient } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter, type InitialEntry } from 'react-router'
import { AppProviders } from '../app/providers'
import { AppRoutes } from '../app/router'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        retryDelay: 0,
        staleTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

export function renderAppAtRoutes(initialEntries: InitialEntry[] = ['/']) {
  const queryClient = createTestQueryClient()
  return {
    queryClient,
    ...render(
      <AppProviders queryClient={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <AppRoutes />
        </MemoryRouter>
      </AppProviders>,
    ),
  }
}
