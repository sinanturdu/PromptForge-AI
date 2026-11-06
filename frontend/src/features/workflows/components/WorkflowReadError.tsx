import { ErrorAlert } from '../../../components/common/ErrorAlert'
import { WorkflowClientError } from '../errors'

export function WorkflowReadError({ error, title }: { readonly error: unknown; readonly title: string }) {
  if (!error) return null
  const safe = error instanceof WorkflowClientError ? error : new WorkflowClientError('network')
  if (safe.kind === 'cancelled') return null
  const message = safe.errorCode === 'workflow_integrity_inconsistent'
    ? 'Kayıtların bütünlüğü doğrulanamadı. Seçilen prompt gösterilmiyor ve kopyalama kapalı. Kayıtlar değiştirilmedi.'
    : safe.errorCode === 'workflow_not_terminal'
      ? 'Sonuç henüz hazır değil. Kayıtlı ilerlemeyi kontrol edebilir ve daha sonra yalnızca okumayı yenileyebilirsin.'
      : `${safe.message} Bu okuma hatası yürütmenin başarısız olduğu anlamına gelmez. Doğrulanamayan içerik gösterilmiyor.`
  return <ErrorAlert title={title} message={message} requestId={safe.requestId} />
}
