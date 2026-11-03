import { EVALUATION_ROUND_STAGES } from '../../../config/platformPreview'

export function PlannedWorkflowSidebar() {
  return (
    <aside
      aria-labelledby="planned-workflow-heading"
      className="flex flex-col justify-between space-y-6 rounded-2xl border border-slate-800/90 bg-slate-900/50 p-5 sm:p-6"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-3.5">
          <div>
            <h2 id="planned-workflow-heading" className="text-sm font-semibold text-slate-100">
              Sınırlandırılmış Değerlendirme ve Revizyon Akışı
            </h2>
            <p className="mt-0.5 text-xs text-slate-400">
              Kaydedilen tercihlerin ilerleyen sürümlerde izleyeceği deterministik süreç
            </p>
          </div>
          <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-[11px] font-medium text-amber-200">
            Planlandı · M6–M8.6
          </span>
        </div>

        <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-3.5 text-xs leading-relaxed text-slate-300">
          <span className="font-semibold text-emerald-300">Şu an aktif olan:</span> Ham prompt,
          proje bağlamı ve optimizasyon tercihleriniz doğrulanarak veritabanına kaydedilir ve
          benzersiz bir UUID ile kalıcı hale getirilir.
        </div>

        <ol className="space-y-3">
          {EVALUATION_ROUND_STAGES.map((stage) => (
            <li
              key={stage.round}
              className="rounded-xl border border-slate-800/90 bg-slate-950/70 p-3.5"
            >
              <div className="flex items-center gap-2.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-indigo-500/40 bg-indigo-500/10 text-xs font-semibold text-indigo-300">
                  {stage.round}
                </span>
                <span className="text-xs font-semibold text-slate-200">{stage.title}</span>
              </div>
              <p className="mt-1.5 pl-7 text-xs leading-relaxed text-slate-400">
                {stage.description}
              </p>
            </li>
          ))}
        </ol>
      </div>

      <div className="rounded-xl border border-dashed border-slate-700/80 bg-slate-950/60 p-4">
        <div className="text-xs font-semibold text-slate-200">
          Derlenmiş Çıktı ve Kalite Raporu Alanı (Henüz Aktif Değil)
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
          Hedefe göre derlenmiş nihai prompt metni, 100 üzerinden deterministik rubrik puanı ve
          tur bazlı revizyon geçmişi Milestone 6–8.5 tamamlandığında detay sayfasında yer alacaktır.
        </p>
      </div>
    </aside>
  )
}
