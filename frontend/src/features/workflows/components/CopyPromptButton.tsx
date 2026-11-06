import { useEffect, useRef, useState } from 'react'
import { readButton } from './resultStyles'

export function CopyPromptButton({ text, disabled }: { readonly text: string; readonly disabled: boolean }) {
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  async function copy() {
    if (disabled || busy) return
    setBusy(true); setFeedback('')
    try {
      await navigator.clipboard.writeText(text)
      if (mounted.current) setFeedback('Prompt kopyalandı.')
    } catch {
      if (mounted.current) setFeedback('Prompt kopyalanamadı. Tarayıcının pano iznini kontrol et.')
    } finally { if (mounted.current) setBusy(false) }
  }
  return <div className="space-y-2">
    <button type="button" className={readButton} disabled={disabled || busy} onClick={() => { void copy() }}>Prompt'u Kopyala</button>
    <p role="status" aria-live="polite" className="min-h-5 text-sm text-slate-300">{feedback}</p>
  </div>
}
