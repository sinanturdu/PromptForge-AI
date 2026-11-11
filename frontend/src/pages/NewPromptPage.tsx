import { PromptSubmitForm } from '../features/prompt-requests/components/PromptSubmitForm'

export function NewPromptPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="space-y-2 border-b border-slate-800/80 pb-6">
        <h1 className="text-2xl font-bold tracking-tight text-slate-50 sm:text-3xl">
          Yeni bir prompt oluştur
        </h1>
        <p className="max-w-2xl text-sm leading-relaxed text-slate-300">
          İsteğini yaz, hedef yapay zekâyı ve detay tercihlerini belirle. Promptunu kaydet ve
          geçmişinden dilediğin zaman görüntüle.
        </p>
      </header>
      <PromptSubmitForm />
      <p className="text-sm leading-relaxed text-slate-400">
        Kaydetmek optimizasyon başlatmaz. Kaydının detaylarından, uygun yerel ortamda ayrıca optimizasyon yapılandırabilirsin.
      </p>
    </div>
  )
}
