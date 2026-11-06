# ADR-005: Süreç İçi Arka Plan Görev Yürütücüsü (`InProcessTaskRunner`)

> **Current implementation clarification (M5F):** the earlier automatic startup
> FAILED/interrupted proposal is superseded by the M5E-5 read-only recovery boundary.
> The actual runner is WorkflowExecutionSupervisor with explicit owned-listener startup,
> not an automatically activated InProcessTaskRunner. Unknown work is never replayed
> or mutated on startup. See [supervision](../execution-supervisor.md) and
> [readback/recovery](../workflow-readback-recovery.md).

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

## Amendment — M5E-2: terminal clarification schema

WorkflowRun now permits terminal CLARIFICATION_REQUIRED, separate from COMPLETED and
FAILED. No WorkflowRun RUNNING state is introduced. Questions belong in versioned
terminal details, not a fabricated PromptVersion/Evaluation. Interrupted execution
will still be FAILED with safe `interrupted` classification. This package adds schema
only: no TaskRunner, recovery, resumption or optimizer persistence hooks. The partial
unique index excludes terminal states so a later explicit workflow can be requested.

## Amendment — M5E-3C: internal atomic finalization

Internal persistence now supports explicit fenced COMPLETED, FAILED and
CLARIFICATION_REQUIRED transitions. Completed evaluations and the validated best
selection survive later failure/clarification; these outcomes create no substitute
version or quality score. Normal completion preserves actual qualification separately
from execution status. Exact committed terminal results can be read for reconciliation
after lease expiry with the same historical token; new writes still require a live
claim and current revision. See [atomic persistence](../atomic-evaluation-persistence.md).
No TaskRunner, optimizer dispatch, API activation, takeover or recovery is implemented.

## Amendment — M5E-4: explicit caller-task execution

`execute_persisted_optimizer()` now adapts the existing sequential M5D loop to the
internal persistence services in the caller's task. It does not implement the planned
TaskRunner or activate automatic API execution. Current-owner lease renewal occurs
only between operations, without a heartbeat or takeover. External cancellation
propagates; unknown in-flight operation/commit outcomes require M5E-5 inspection,
not automatic provider replay or fabricated terminal status. Confirmed prior rounds
remain durable. See [execution integration](../persistent-optimizer-execution.md).

## Amendment — M5E-5: read-only recovery assessment

Independent internal snapshots now diagnose active/expired claims, unresolved
operations, committed artifacts and contradictory histories. They do not implement
the planned startup FAILED/interrupted mutation, a TaskRunner, takeover or replay.
Database commitment and caller acknowledgment are separate; missing Evaluation or
expired lease never proves a paid call is safe to repeat. Recovery recommendations
grant no mutation authority. See [readback and assessment](../workflow-readback-recovery.md).

