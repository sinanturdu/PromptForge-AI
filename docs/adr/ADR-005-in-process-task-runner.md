# ADR-005: Süreç İçi Arka Plan Görev Yürütücüsü (`InProcessTaskRunner`)

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 6)

## Bağlam

Çok turlu LLM çağrılarının tek bir senkron HTTP isteği içinde beklenmesi zaman aşımı riskine yol açar; öte yandan Redis veya Celery kurulumu yerel MVP için aşırı yük getirir.

## Karar

Çalıştırmalar `TaskRunner` arayüzü arkasındaki süreç içi `InProcessTaskRunner` (asyncio görevi) ile arka planda yürütülür ve istemciye `202 Accepted` döner. Uygulama yeniden başlatıldığında yarıda kalmış çalıştırmalar başlangıçta `FAILED` (`interrupted`) olarak işaretlenir.

## Sonuçlar

- Harici mesaj kuyruğu olmadan güvenli asenkron yürütme sağlanır; ölçek gerektiğinde `TaskRunner` arkasından harici kuyruğa geçilebilir.

## Amendment — M5D-4: failed execution with retained evaluations

The earlier workflow diagram in architecture section 7.5 described later-round
errors as `COMPLETED/error_partial`. That draft behavior is explicitly superseded:
an expected later Architect/Critic/validation/deadline failure means failed
execution, while earlier completed evaluations and their best selection remain
available. Retaining a candidate does not make execution successful or approve
its quality. Failure before the first completion has no selected candidate.

M5D-4 implements this policy only in the opt-in in-memory optimizer, running in the
caller task. External cancellation propagates unchanged. It adds no background
TaskRunner, recovery process, persistent status or API. The original planned
interrupted-run policy remains FAILED (`interrupted`) when persistence and a runner
are introduced. See [optimizer behavior and persistence gaps](../prompt-optimizer.md).

