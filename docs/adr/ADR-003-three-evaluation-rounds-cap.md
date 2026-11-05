# ADR-003: En Fazla Üç Değerlendirme Turu Sınırı (`evaluation_round` 1–3)

- **Durum:** Onaylandı (`MAX_EVALUATION_ROUNDS_HARD_LIMIT = 3` ve veritabanı `CHECK` kısıtları M3B'de uygulandı; revizyon iş akışı M8'de uygulanacak)

## Bağlam

"İterasyon" terimi ilk üretimin dahil olup olmadığı konusunda belirsizlik yaratabilmektedir. Ayrıca değerlendirme döngüsünün kesin bir üst sınıra sahip olması gerekmektedir.

## Karar

Kod tabanında ve veritabanında `iteration` yerine yalnızca `evaluation_round` terimi kullanılır:

1. **Tur 1:** İlk üretim (`create`) + 1. değerlendirme
2. **Tur 2:** 1. revizyon (`revise`) + 2. değerlendirme
3. **Tur 3:** 2. revizyon (`revise`) + 3. (son) değerlendirme

Üçüncü değerlendirmeden sonra iş akışı koşulsuz durur. Bu sınır yapılandırma doğrulaması, kod sabiti (`MAX_EVALUATION_ROUNDS_HARD_LIMIT = 3`) ve veritabanı `CHECK` / `UNIQUE` kısıtlarıyla üç katmanda korunur.

## Sonuçlar

- Bir çalıştırmada en fazla 3 prompt sürümü ve 3 değerlendirme oluşur; maliyet ve süre öngörülebilir kalır.
- Milestone 3B'de `workflow_runs` (`max_evaluation_rounds BETWEEN 1 AND 3`, `current_round` ve `completed_rounds` aralık kısıtları), `prompt_versions` (`evaluation_round IS NULL OR evaluation_round BETWEEN 1 AND 3`) ve `agent_runs` (`evaluation_round IS NULL OR evaluation_round BETWEEN 1 AND 3`) tablolarında bu sınır veritabanı düzeyinde aktif hale getirilmiştir; `evaluations` tablosundaki karşılığı Milestone 7'de eklenecektir.

## Amendment — M5D-3: completed evaluations versus dispatches

The new in-memory `EvaluationHistory` contains only one to three successfully
completed, validated evaluations, consecutively numbered from 1. A generated
candidate, clarification, failed/cancelled dispatch or gateway retry is not a
completed evaluation and cannot fill a history entry. Before the first completion
there is no history. Revision ancestry points to an earlier completed baseline.

This distinction does not authorize extra model calls after a failure. The future
executor must separately bound/record actual dispatch attempts and deadlines, stop
after at most three completed evaluations, and obey ADR-005 failure/interruption
handling. Selection/progress helpers dispatch nothing, persist no counters and do
not implement the workflow. Clarification has no completed candidate/version.
See [M5D-3 contracts](../optimization-policies.md) for the current implementation
and remaining transaction/lifecycle work.

## Amendment — M5E-4: committed rounds in explicit persistent execution

The opt-in persistent adapter reuses the M5D loop and publishes a round only after
atomic Evaluation persistence and validated history readback. Reservations, provider
retries/repairs, staged candidates, clarification and unchanged revisions do not
increase the completed count. Existing limits/stops remain unchanged; no background
execution or recovery is introduced. See [execution integration](../persistent-optimizer-execution.md).

