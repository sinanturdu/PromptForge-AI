interface CharacterCounterProps {
  readonly id?: string
  readonly currentLength: number
  readonly maxLength: number
}

export function CharacterCounter({ id, currentLength, maxLength }: CharacterCounterProps) {
  const isOverLimit = currentLength > maxLength
  const isNearLimit = !isOverLimit && currentLength >= maxLength * 0.9

  const toneClassName = isOverLimit
    ? 'border-rose-500/50 bg-rose-950/60 text-rose-200 font-semibold'
    : isNearLimit
      ? 'border-amber-500/40 bg-amber-950/40 text-amber-200'
      : 'border-slate-800 bg-slate-900/80 text-slate-400'

  return (
    <span
      id={id}
      aria-live="polite"
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-xs tabular-nums transition-colors ${toneClassName}`}
    >
      {currentLength.toLocaleString('tr-TR')} / {maxLength.toLocaleString('tr-TR')}
    </span>
  )
}
