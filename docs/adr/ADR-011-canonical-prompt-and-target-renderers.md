# ADR-011: CanonicalPrompt Ara Temsili ve Hedefe Özgü Prompt Derleyicileri

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 6 ve Milestone 8.5)

## Bağlam

Farklı yapay zekâ model aileleri (`generic` / Universal, `openai` / ChatGPT, `anthropic` / Claude, `google` / Gemini) yapısal bölümleme ve örnek yerleşimi konusunda farklı belgelendirilmiş pratiklere sahiptir. Ancak biçimlendiricinin denetlenmemiş içerik uydurması veya tek bir genel değerlendirmenin tüm hedeflerde kaliteyi kanıtladığının varsayılması mimari bütünlüğü bozar.

## Karar

1. `PromptEngineerAgent`, prompt içeriğini yapılandırılmış `CanonicalPrompt` ara temsilinde (`role`, `context`, `input_data`, `task`, `requirements`, `constraints`, `prohibitions`, `output_format`, `examples`, `variables`, `assumptions`) üretir.
2. Hedefe özgü içerik kararları (ör. örnekler veya ek kısıtlar) yalnızca `PromptEngineerAgent` tarafından üretilir.
3. Sürümlü ve deterministik derleyiciler (`GenericPromptRenderer`, `OpenAIPromptRenderer`, `AnthropicPromptRenderer`, `GeminiPromptRenderer`) `CanonicalPrompt` verisini nihai metne dönüştürür; yeni gereksinim veya doğrulanmamış örnek uyduramaz, önemli bilgiyi silemez.
4. Teslim edilen nihai prompt bizzat `PromptCriticAgent` tarafından değerlendirilmiş sürümdür ve değerlendirme kaydı `evaluated_target_mode` ile `evaluated_renderer_version` bilgisini taşır.
5. Kullanıcı değerlendirme sonrasında yeni bir değerlendirme çalıştırmadan hedef değiştirirse, üretilen varyant açıkça **"bağımsız olarak değerlendirilmedi"** şeklinde etiketlenir. XML, Markdown veya few-shot biçimlerinin otomatik üstünlük sağladığı varsayılmaz.

## Sonuçlar

- İçerik üretimi ile sözdizimsel derleme temiz biçimde ayrılır; her renderer bağımsız birim testleriyle doğrulanabilir ve değerlendirme izlenebilirliği korunur.

