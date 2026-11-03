import type { ReactNode } from 'react'
import type { LocalizedValidationIssue } from '../../lib/api-client'

interface ErrorAlertProps {
  readonly title: string
  readonly message: string
  readonly validationIssues?: readonly LocalizedValidationIssue[]
  readonly requestId?: string | null
  readonly actions?: ReactNode
}

export function ErrorAlert({
  title,
  message,
  validationIssues = [],
  requestId = null,
  actions,
}: ErrorAlertProps) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-rose-500/40 bg-rose-950/35 p-4 text-sm text-rose-100 shadow-sm sm:p-5"
    >
      <div className="space-y-2.5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <p className="font-semibold text-rose-200">{title}</p>
          {requestId ? (
            <span className="rounded border border-rose-400/30 bg-rose-950/70 px-2 py-0.5 font-mono text-xs text-rose-200">
              İstek Kimliği: {requestId}
            </span>
          ) : null}
        </div>

        <p className="leading-relaxed text-rose-100/90">{message}</p>

        {validationIssues.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-xs text-rose-100/95">
            {validationIssues.map((issue, index) => (
              <li key={`${issue.field}-${issue.code}-${index}`}>
                <span className="font-semibold">{issue.fieldLabel}:</span> {issue.message}
              </li>
            ))}
          </ul>
        ) : null}

        {actions ? <div className="pt-1">{actions}</div> : null}
      </div>
    </div>
  )
}
