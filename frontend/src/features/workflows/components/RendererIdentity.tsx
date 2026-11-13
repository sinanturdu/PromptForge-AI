import { TARGET_AI_FAMILY_OPTIONS } from '../../prompt-requests/types'
import type { WorkflowVersionSummary } from '../contracts'

export function RendererIdentity({ version }: { readonly version: WorkflowVersionSummary }) {
  const target = TARGET_AI_FAMILY_OPTIONS.find(option => option.value === version.target_mode)
  return <dl aria-label="Renderer kimliği" className="grid min-w-0 gap-2 text-xs text-slate-300 [overflow-wrap:anywhere] sm:grid-cols-3">
    <div className="min-w-0">
      <dt className="text-slate-400">Hedef Yapay Zekâ</dt>
      <dd className="mt-1">{target ? <>{target.label} (<code>{version.target_mode}</code>)</> : <code>{version.target_mode}</code>}</dd>
    </div>
    <div className="min-w-0">
      <dt className="text-slate-400">Değerlendirilen Renderer</dt>
      <dd className="mt-1"><code>{version.renderer_name}</code></dd>
    </div>
    <div className="min-w-0">
      <dt className="text-slate-400">Renderer Sürümü</dt>
      <dd className="mt-1"><code>{version.renderer_version}</code></dd>
    </div>
  </dl>
}
