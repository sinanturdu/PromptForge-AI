import { useEffect, useState, useSyncExternalStore } from 'react'

function subscribe(listener: () => void) {
  document.addEventListener('visibilitychange', listener)
  return () => document.removeEventListener('visibilitychange', listener)
}
export function useProgressVisibility() {
  const documentVisible = useSyncExternalStore(subscribe, () => document.visibilityState !== 'hidden', () => true)
  const [node, ref] = useState<HTMLElement | null>(null)
  const [inView, setInView] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (!node || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => setInView(entries.some(entry => entry.isIntersecting)))
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])
  return { ref, visible: documentVisible && inView }
}
