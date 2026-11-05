# ADR-011: CanonicalPrompt Ara Temsili ve Hedefe Özgü Prompt Derleyicileri

- **Durum:** Onaylandı (M5B-2: sparse CanonicalPrompt ve GenericPromptRenderer uygulandı; hedefe özgü renderer'lar ve Critic entegrasyonu planlandı)

## Bağlam

Farklı yapay zekâ model aileleri (`generic` / Universal, `openai` / ChatGPT, `anthropic` / Claude, `google` / Gemini) yapısal bölümleme ve örnek yerleşimi konusunda farklı belgelendirilmiş pratiklere sahiptir. Ancak biçimlendiricinin denetlenmemiş içerik uydurması veya tek bir genel değerlendirmenin tüm hedeflerde kaliteyi kanıtladığının varsayılması mimari bütünlüğü bozar.

## Karar

1. Önceden `PromptEngineerAgent` adıyla planlanan üretim rolü M5B-2'de `PromptArchitectAgent` olarak uygulanır. Prompt içeriği tek `CanonicalPrompt` ara temsilinde (`role`, `context`, `input_data`, `task`, `requirements`, `constraints`, `prohibitions`, `output_format`, `examples`, `variables`, `assumptions`) tutulur. Yalnızca `task` içerik olarak zorunludur; diğer alanlar null veya boş olabilir ve zorunlu görünür bölümler oluşturmaz.
2. Hedefe özgü içerik kararlarını Architect verir; renderer yeni içerik üretmez. Örnekler, varsayımlar, roller ve ek kısıtlar kullanıcı isteğine dayanmalı veya açıkça yetkilendirilmiş olmalıdır; uydurulamaz. Low/Medium/High yararlı ayrıntı düzeyidir, uzunluk hedefi değildir.
3. Sürümlü ve deterministik derleyiciler (`GenericPromptRenderer`, `OpenAIPromptRenderer`, `AnthropicPromptRenderer`, `GeminiPromptRenderer`) `CanonicalPrompt` verisini nihai metne dönüştürür; yeni gereksinim veya doğrulanmamış örnek uyduramaz, önemli bilgiyi silemez.
4. Teslim edilen nihai prompt bizzat `PromptCriticAgent` tarafından değerlendirilmiş sürümdür ve değerlendirme kaydı `evaluated_target_mode` ile `evaluated_renderer_version` bilgisini taşır.
5. Kullanıcı değerlendirme sonrasında yeni bir değerlendirme çalıştırmadan hedef değiştirirse, üretilen varyant açıkça **"bağımsız olarak değerlendirilmedi"** şeklinde etiketlenir. XML, Markdown veya few-shot biçimlerinin otomatik üstünlük sağladığı varsayılmaz.

## Sonuçlar

M5B-2'de Architect `preserve`, `rewrite` veya `clarify` kararı verir. `preserve`
orijinal `raw_prompt` metnini aynen döndürür. `rewrite` seyrek canonical içeriği
deterministik olarak derler; açıklamalar ve değişiklikler nihai prompta eklenmez.
`clarify` yalnızca sadık bir sonuç üretmeyi engelleyen belirsizlikler için soru döndürür.
Tüm hedefler şimdilik aynı `GenericPromptRenderer` sürümünü kullanır. Critic ve
hedefe özgü renderer'lar uygulanana kadar bağımsız kalite onayı iddia edilmez;
yukarıdaki değerlendirme bütünlüğü kuralları sonraki entegrasyonlar için korunur.
Veritabanındaki `engineer_create` / `engineer_revise` kaynak değerleri değiştirilmez.

- İçerik üretimi ile sözdizimsel derleme temiz biçimde ayrılır; her renderer bağımsız birim testleriyle doğrulanabilir ve değerlendirme izlenebilirliği korunur.

