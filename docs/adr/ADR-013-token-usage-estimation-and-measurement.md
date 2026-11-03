# ADR-013: Token Kullanım Tahmini ve Ölçüm Mimarisi

- **Durum:** Onaylandı (Planlanan uygulama: Post-MVP / Milestone 10+; iç ajan sayaçları M5–M6'da kaydedilir)

## Bağlam

Prompt optimizasyonunda iç ajan yürütme maliyeti ile derlenen promptun hedef uygulamada tüketeceği token miktarı sıkça karıştırılmaktadır. Ayrıca tüm sağlayıcılar harici kullanımdaki görünmeyen akıl yürütme tokenlarını önceden tahmin etmeye izin vermez.

## Karar

Gelecekteki `TokenAnalysis` modülü altı metriği birbirinden ayırır:

1. Prompt girdi tokenları (`prompt_input_tokens`)
2. Beklenen görünür çıktı uzunluğu aralığı
3. Destekleyen sağlayıcıların raporladığı akıl yürütme tokenları (`reasoning_tokens`)
4. Doğrudan API çağrılarındaki gerçek kullanım (`actual_api_usage`)
5. Harici kullanım için kaba aşağı akış tahmini (`estimated_downstream_usage`)
6. PromptForge iç ajan yürütme tüketimi (`internal_agent_execution_usage`)

Girdi sayımında mevcut olduğunda sağlayıcıya özgü resmi token sayım API'leri kullanılır; yedek (fallback) tahminleyiciler sonuçlarını açıkça **yaklaşık değer** olarak işaretler. Aşağı akış toplam kullanımı kesin tahmin gibi sunulmaz ve "%60 garanti tasarruf" gibi desteksiz iddialara yer verilmez.

## Sonuçlar

- Maliyet ve uzunluk metrikleri teknik olarak dürüst, şeffaf ve doğrulanabilir bir zeminde raporlanır.

