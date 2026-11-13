import { useContext, useLayoutEffect, useRef } from 'react'
import { matchPath, UNSAFE_NavigationContext, useLocation, type Location } from 'react-router'
import { ROUTES } from '../../app/routes'
import { readAttempt } from './attempts'
import { sameId } from './readValidation'

export function useFollowupRoute(parentId: string, workflowId: string, defaultSelected: boolean) {
  const location = useLocation()
  // BrowserRouter and MemoryRouter expose the live history location on their
  // navigator. The React location can lag behind a concurrent route transition.
  const { navigator } = useContext(UNSAFE_NavigationContext)
  const history = navigator as typeof navigator & { readonly location?: Location }
  const binding = useRef<{ location: Location; parentId: string; workflowId: string; defaultSelected: boolean } | null>(null)

  function routeMatches() {
    const live = history.location
    if (!live || live.key !== location.key || live.pathname !== location.pathname ||
      live.search !== location.search || live.hash !== location.hash) return false
    const parent = matchPath(ROUTES.promptRequestDetailPattern, live.pathname)?.params.id
    const search = new URLSearchParams(live.search)
    const workflows = search.getAll('workflow')
    const views = search.getAll('view')
    if (!parent || !sameId(parent, parentId) || views.length !== 1 || views[0] !== 'result') return false
    if (workflows.length > 0) return workflows.length === 1 && sameId(workflows[0]!, workflowId)
    try {
      const attempt = defaultSelected ? readAttempt(parentId) : null
      return Boolean(attempt && (attempt.start === 'accepted' || attempt.start === 'unknown') &&
        sameId(attempt.workflowId, workflowId))
    } catch {
      return false
    }
  }

  useLayoutEffect(() => {
    // A history entry can be revisited with the same key. Revoke the mounted
    // editor on departure even if React has yet to render the intervening URL.
    binding.current = { location, parentId, workflowId, defaultSelected }
    const leave = () => { binding.current = null }
    const navigation = (window as Window & { navigation?: EventTarget }).navigation
    navigation?.addEventListener('currententrychange', leave)
    window.addEventListener('popstate', leave)
    window.addEventListener('pagehide', leave)
    return () => {
      leave()
      navigation?.removeEventListener('currententrychange', leave)
      window.removeEventListener('popstate', leave)
      window.removeEventListener('pagehide', leave)
    }
  }, [location, parentId, workflowId, defaultSelected])
  function isCurrent() {
    const current = binding.current
    return current?.location === location && current.parentId === parentId && current.workflowId === workflowId &&
      current.defaultSelected === defaultSelected && routeMatches()
  }
  return { isCurrent, entryKey: location.key }
}
