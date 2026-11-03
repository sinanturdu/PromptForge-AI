export function StatusBanner() {
  return (
    <section
      aria-label="Uygulama aşaması bilgilendirmesi"
      className="rounded-xl border border-indigo-500/30 bg-indigo-950/30 p-4 sm:p-5"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <p className="text-sm font-semibold text-indigo-200">
            Prompt Gönderimi ve Kalıcı Kayıt Aktif — AI Optimizasyon Ajanları Planlama Aşamasında
          </p>
          <p className="text-sm leading-relaxed text-slate-300">
            Bu sürümde (Milestone 4A) ham prompt isteklerinizi, proje bağlamınızı ve hedef model
            tercihlerinizi gerçek FastAPI + SQLite altyapısına kaydedip inceleyebilirsiniz.{' '}
            <span className="text-slate-200">
              Prompt Engineer üretimi, CanonicalPrompt derleyicileri, bağımsız Prompt Critic
              değerlendirmesi ve otomatik revizyon döngüsü
            </span>{' '}
            henüz etkin değildir; Milestone 5–8.6 aşamalarında devreye alınacaktır.
          </p>
        </div>
        <span className="shrink-0 self-start rounded-md border border-indigo-400/30 bg-indigo-500/15 px-3 py-1 text-xs font-medium text-indigo-200 sm:self-center">
          Canlı LLM Çağrısı Yok
        </span>
      </div>
    </section>
  )
}
