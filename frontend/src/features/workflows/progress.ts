import { isTerminal, type WorkflowProgress, type WorkflowStatus } from './contracts'

export const workflowStatusLabels: Readonly<Record<WorkflowStatus, string>> = {
  PENDING: 'Başlatılmayı bekliyor', GENERATING: 'Prompt hazırlanıyor', EVALUATING: 'Prompt değerlendiriliyor',
  REVISING: 'Prompt iyileştiriliyor', COMPLETED: 'Yürütme tamamlandı', FAILED: 'İşlem başarısız',
  CLARIFICATION_REQUIRED: 'Ek açıklama gerekiyor',
}
export function validWorkflowId(value: string | null | undefined): value is string {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}
export function trustworthyProgress(row: WorkflowProgress): boolean {
  return row.history_valid && !row.records_truncated && row.findings.length === 0
}
export function progressExplanation(row: WorkflowProgress): string {
  if (!trustworthyProgress(row)) return 'Kaydın bütünlüğü incelenmeli. Tamamlanma ve kalite sonucu güvenilir olarak sunulmuyor; otomatik izleme durdu.'
  if (row.status === 'PENDING') return 'Henüz kalıcı bir yürütme başlangıcı görünmüyor. HTTP 202 görev kabulünü bildirir; PENDING bir kuyruk veya yürütme garantisi değildir. Bu durum sürerse güvenli GET ile yenile; yeniden başlatma gönderilmez.'
  if (row.status === 'FAILED') return 'Sunucu başarısız bir yürütme kaydetti. Yeni başlatma veya otomatik kurtarma yapılmaz; mevcut kayıtlar inceleme için korunur.'
  if (row.status === 'CLARIFICATION_REQUIRED') return 'Devam etmek için ek kullanıcı açıklaması gerekiyor. Otomatik izleme durdu; bu ekran soru veya yeni yanıt uydurmaz.'
  if (isTerminal(row.status)) return 'Kalıcı yürütme tamamlandı. Bu, promptun kalite koşullarını sağladığı anlamına gelmez. Otomatik izleme durdu.'
  return 'Bu, son kalıcı durumdur; tarayıcının yenileme yapması bir modelin şu anda çalıştığını kanıtlamaz. En fazla tur sınırının tamamı kullanılmak zorunda değildir.'
}
