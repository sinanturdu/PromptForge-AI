# Mimari Karar Kayıtları (ADR)

Bu dizin, PromptForge AI mimarisinde onaylanan temel tasarım kararlarını içerir. Ayrıntılı sistem tasarımı için [`../architecture.md`](../architecture.md) dokümanına bakınız.

> **Not:** Kararların "Onaylandı" durumunda olması mimari tasarımın kabul edildiğini gösterir; her kararın kod düzeyinde uygulanacağı aşama ilgili ADR içinde belirtilmiştir. Şu anda **Milestone 2A**, **Milestone 3A** ve **Milestone 3B** tamamlanmıştır.

| ADR | Başlık | Uygulama Aşaması |
|---|---|---|
| [ADR-001](./ADR-001-modular-monolith.md) | Modüler Monolit Mimarisi | M2A (Temel) – M9 |
| [ADR-002](./ADR-002-deterministic-revision-workflow.md) | Deterministik Revizyon İş Akışı (Kod Tabanlı Orkestrasyon) | M6 – M8 |
| [ADR-003](./ADR-003-three-evaluation-rounds-cap.md) | En Fazla Üç Değerlendirme Turu Sınırı (`evaluation_round` 1–3) | M3B (DB Kısıtları Uygulandı) – M8 (İş Akışı) |
| [ADR-004](./ADR-004-http-polling-for-mvp.md) | İlk MVP İçin HTTP Polling İletişimi | M4 – M6 |
| [ADR-005](./ADR-005-in-process-task-runner.md) | Süreç İçi Arka Plan Görev Yürütücüsü (`InProcessTaskRunner`) | M6 |
| [ADR-006](./ADR-006-provider-independent-llm-layer.md) | Sağlayıcıdan Bağımsız LLM Katmanı ve Ajan Bazlı Model Ayarları | M5 |
| [ADR-007](./ADR-007-best-version-revision-baseline.md) | En İyi Değerlendirilmiş Sürümü Temel Alan Revizyon Stratejisi | M8 |
| [ADR-008](./ADR-008-local-unauthenticated-mvp.md) | İlk MVP'de Kimlik Doğrulamasız Yerel Çalışma Modu | M2A – M9 |
| [ADR-009](./ADR-009-sqlite-to-postgresql-portability.md) | SQLite MVP Şeması ve PostgreSQL Taşınabilirliği | M3A (Bağlantı) & M3B (İlk 4 Tablo & Alembic Uygulandı) – M7 (Değerlendirme Tabloları) |
| [ADR-010](./ADR-010-independent-critic-and-deterministic-scoring.md) | Bağımsız Prompt Critic ve Deterministik Puanlama Motoru | M7 |
| [ADR-011](./ADR-011-canonical-prompt-and-target-renderers.md) | CanonicalPrompt Ara Temsili ve Hedefe Özgü Prompt Derleyicileri | M6 & M8.5 |
| [ADR-012](./ADR-012-prompt-response-and-reasoning-preferences.md) | Prompt Detay Seviyesi, Yanıt Detayı ve Akıl Yürütme Tercihleri | M8.6 |
| [ADR-013](./ADR-013-token-usage-estimation-and-measurement.md) | Token Kullanım Tahmini ve Ölçüm Mimarisi | Post-MVP (M10+) |
| [ADR-014](./ADR-014-future-speech-to-text-architecture.md) | Gelecek Sesli Girdi (Speech-to-Text) Mimarisi | Post-MVP (M10+) |

