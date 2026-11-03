import { ArchitecturePillars } from '../components/ArchitecturePillars'
import { StatusBanner } from '../components/StatusBanner'
import { PlannedWorkflowSidebar } from '../features/prompt-requests/components/PlannedWorkflowSidebar'
import { PromptSubmitForm } from '../features/prompt-requests/components/PromptSubmitForm'

export function NewPromptPage() {
  return (
    <div className="space-y-8">
      <section className="max-w-3xl space-y-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">
          Yapılandırılmış Prompt Mühendisliği ve Derleme Çalışma Alanı
        </p>
        <h1 className="text-2xl font-bold tracking-tight text-slate-50 sm:text-3xl lg:text-4xl">
          Ham isteklerinizi yapılandırılmış ve hedefe hazır prompt kayıtlarına dönüştürün.
        </h1>
        <p className="text-sm leading-relaxed text-slate-300 sm:text-base">
          PromptForge AI; kullanıcı niyetini analiz etmek, yapılandırılmış CanonicalPrompt üretmek,
          bağımsız kalite denetimi uygulamak ve Universal, ChatGPT / GPT, Claude ile Gemini
          iş akışları için deterministik çıktılar derlemek üzere tasarlanmış modüler çoklu ajan
          platformudur.
        </p>
      </section>

      <StatusBanner />

      <div className="grid items-start gap-6 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <PromptSubmitForm />
        </div>
        <div className="lg:col-span-5">
          <PlannedWorkflowSidebar />
        </div>
      </div>

      <ArchitecturePillars />
    </div>
  )
}
