# ADR-006: Sağlayıcıdan Bağımsız LLM Katmanı ve Ajan Bazlı Model Ayarları

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 5)

## Bağlam

Uygulamanın tek bir yapay zekâ sağlayıcısına kilitlenmemesi, otomatik testlerin gerçek API çağrısı olmadan deterministik çalışması ve `PromptEngineerAgent` ile `PromptCriticAgent` için farklı model/parametre seçilebilmesi gerekmektedir.

## Karar

`LLMProvider` protokolü ve `LLMGateway` katmanı oluşturulur. MVP'de deterministik testler için `FakeLLMProvider` ve üretim akışı için **bir gerçek sağlayıcı adaptörü** uygulanır; ikinci sağlayıcı Post-MVP'de eklenecektir. Her ajan bağımsız model, sıcaklık ve çıktı sınırı yapılandırmasına sahiptir.

## Sonuçlar

- Testler hızlı, ücretsiz ve tekrarlanabilir çalışır; öz-tercih yanlılığını (self-preference bias) azaltmak için Critic farklı bir modelle yapılandırılabilir.

