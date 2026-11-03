# ADR-005: Süreç İçi Arka Plan Görev Yürütücüsü (`InProcessTaskRunner`)

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 6)

## Bağlam

Çok turlu LLM çağrılarının tek bir senkron HTTP isteği içinde beklenmesi zaman aşımı riskine yol açar; öte yandan Redis veya Celery kurulumu yerel MVP için aşırı yük getirir.

## Karar

Çalıştırmalar `TaskRunner` arayüzü arkasındaki süreç içi `InProcessTaskRunner` (asyncio görevi) ile arka planda yürütülür ve istemciye `202 Accepted` döner. Uygulama yeniden başlatıldığında yarıda kalmış çalıştırmalar başlangıçta `FAILED` (`interrupted`) olarak işaretlenir.

## Sonuçlar

- Harici mesaj kuyruğu olmadan güvenli asenkron yürütme sağlanır; ölçek gerektiğinde `TaskRunner` arkasından harici kuyruğa geçilebilir.

