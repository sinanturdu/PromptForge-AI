# ADR-011: CanonicalPrompt Ara Temsili ve Hedefe Özgü Prompt Derleyicileri

- **Durum:** Onaylandı (M5B-2: sparse CanonicalPrompt ve GenericPromptRenderer; M5C-2: bellek içi bağımsız Critic uygulandı. Hedef renderer'ları, workflow ve kalıcılık planlandı.)

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
Tüm hedefler şimdilik aynı `GenericPromptRenderer` sürümünü kullanır. M5C-2 Critic
yalnızca açıkça çağrıldığında kesin derlenmiş metni kör değerlendirir; Architect
çıktısı tek başına bağımsız onay değildir. Rubric kapısını geçmek gerçek dünya
başarısının kanıtı değildir. Hedef renderer'ları ve kalıcı workflow entegrasyonu
planlanmaktadır; yukarıdaki değerlendirme bütünlüğü kuralları korunur.
Veritabanındaki `engineer_create` / `engineer_revise` kaynak değerleri değiştirilmez.

M5D-2 adds a separate [feedback-driven revision operation](../architect-revision.md).
Revision preserve retains the selected baseline, while initial create preserve
still retains raw_prompt exactly. A changed revision uses the same sparse canonical
model/renderer and returns CandidateReady(decision="rewrite") without quality approval.
Selection, workflow rounds and persistence remain separate future responsibilities.

- İçerik üretimi ile sözdizimsel derleme temiz biçimde ayrılır; her renderer bağımsız birim testleriyle doğrulanabilir ve değerlendirme izlenebilirliği korunur.

## Amendment — M6-2 OpenAI renderer pilot

`OpenAIPromptRenderer` 1.0.0 returns task-only content verbatim. With additional
content it adds a Markdown level-two heading to each populated group, in this
order: Task, Role, Context, Input, Requirements, Constraints, Prohibitions,
Output format, Examples, Variables, Assumptions. Groups and examples use blank
line separators. Other list items use single newlines. Each authored value is
inserted unchanged, including its whitespace. Headings are formatting only.

New OpenAI-target workflows use the pilot. Other targets and historical
workflows retain the generic renderer. The existing version-one configuration
field `optimizer_version` distinguishes the historical policy (`1.0.0`) from
new OpenAI workflow creation (`1.1.0`). Historical workflows keep their
source-only digest. New OpenAI workflows bind the exact source digest and
optimizer policy under a versioned workflow digest domain in the existing
`request_content_digest` field. Replay, claim, operation reservation and
readback verify that binding even before a candidate exists.
Version-one candidate JSON keeps its existing keys and now accepts either
supported identity, subject to target compatibility. Revisions use the evaluated
baseline renderer, and readback revalidates the recorded renderer and text.
The Critic length limit adds only a fixed allowance for the pilot headings.
Pre-M6-2 code rejects `optimizer_version=1.1.0` records, including `PENDING`
workflows without a candidate. Rolling back application code after creating
these records requires a compatible reader or a separately designed recovery
procedure. Editing the stored policy fails the binding check.

