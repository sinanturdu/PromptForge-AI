# ADR-002: Deterministik Revizyon İş Akışı (Kod Tabanlı Orkestrasyon)

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 6 – Milestone 8)

## Bağlam

Çoklu ajan sistemlerinde döngü kontrolünün bir LLM orkestratörüne bırakılması öngörülemez çalışma sürelerine, sonsuz döngü riskine ve yüksek maliyete yol açabilir.

## Karar

MVP'de ajan koordinasyonu harici bir ajan çerçevesi (LangGraph, CrewAI vb.) veya LLM karar vericisi yerine doğrudan Python kodundaki deterministik `RevisionWorkflow` durum makinesi tarafından yürütülür. `PromptEngineerAgent` ve `PromptCriticAgent` yalnızca yapılandırılmış girdi alıp çıktı üreten yalıtılmış bileşenlerdir.

## Sonuçlar

- Döngünün sonlanması matematiksel ve test edilebilir biçimde garanti altına alınır.
- Gereksiz çerçeve bağımlılıkları önlenir.

