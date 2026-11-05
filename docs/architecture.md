# PromptForge AI — Teknik Mimari Dokümantasyonu (v1.2)

> **Uygulama Durumu Bildirimi (M5C-2):** Temel API/veritabanı ve M4 frontend/E2E altyapısına ek olarak M5A LLMGateway/sağlayıcılar, M5B PromptArchitectAgent, sparse CanonicalPrompt/GenericPromptRenderer, 36 vakalık offline benchmark ve bellek içi entegrasyon sınırı uygulanmıştır. M5C-2 bağımsız PromptCriticAgent ve deterministik ScoringEngine ekler. Ajanlar mevcut API'lerde etkinleştirilmez ve sonuçları kaydedilmez. Workflow, değerlendirme tabloları, hedefe özgü renderer'lar, REA, token analizi ve sesli girdi halen planlanmaktadır. Aşağıdaki eski milestone numaraları hedef yol haritasını anlatır; güncel Critic sözleşmesi için [M5C-2 dokümanına](prompt-critic.md) bakınız.

---

## 1. Proje Vizyonu

**PromptForge AI**, sıradan kullanıcı isteklerini yapılandırılmış, bağımsız olarak denetlenmiş ve hedef yapay zekâ modeline uygun biçimde derlenmiş yüksek kaliteli promptlara dönüştüren modüler bir çoklu ajan (multi-agent) platformudur.

Platform basit bir sohbet arayüzü değildir; temel ilkeleri şunlardır:

- **Sorumluluk ayrımı:** Üretim (`PromptEngineerAgent`) ve kalite değerlendirmesi (`PromptCriticAgent`) birbirinden bağımsız talimat ve model yapılandırmalarıyla çalışır. Hiçbir ajan kendi çıktısını bağımsız kalite onayı olarak değerlendiremez.
- **Deterministik kontrol:** Puan hesaplaması ve revizyon döngüsü kararları LLM'e bırakılmaz, uygulama kodunda deterministik kurallarla yürütülür.
- **Ara temsil ve hedef derleme (Prompt Compiler):** Prompt içeriği yapılandırılmış `CanonicalPrompt` ara temsilinde üretilir ve deterministik `PromptRenderer` modülleriyle hedef ortama (`generic`, `openai`, `anthropic`, `google`) dönüştürülür.
- **Uzun vadeli genişleme:** Mimari, ilerleyen fazlarda yazılım projelerini planlayan, geliştiren, test eden ve güvenlik açısından inceleyen 13 uzman ajana genişleyebilecek sınırlarla tasarlanmıştır.

---

## 2. Kapsam ve Uygulama Durumu

### 2.1 Mevcut Durum — Milestone 2A, 3A, 3B, 3C-1, 3C-2, 4A, 4B-1 & 4B-2 (Uygulandı)

- `backend/` ve `frontend/` ayrımına sahip monorepo yapısı
- Python 3.12, `uv`, FastAPI uygulama fabrikası (`create_app`), `lifespan` kaynak yönetimi ve `GET /api/v1/health` uç noktası
- `pydantic-settings` ortam yapılandırması (`DATABASE_URL`, `LOG_LEVEL`, `MAX_REQUEST_BODY_BYTES`, varsayılan `backend/data/promptforge.db`) ve SQLAlchemy 2.0 Async + `aiosqlite` oturum altyapısı (`DatabaseSessionManager`, `get_db_session`, yabancı anahtar kısıtları ve dosya tabanlı SQLite için WAL modu)
- SQLAlchemy 2.0 `DeclarativeBase` (`app/db/base.py`), ilk dört ORM modeli (`PromptRequest`, `WorkflowRun`, `PromptVersion`, `AgentRun`) ve CWD'den bağımsız asenkron Alembic göç sistemi (`0001_initial_schema`)
- Prompt yönetimi REST API uç noktaları (`POST /api/v1/prompt-requests`, `GET /api/v1/prompt-requests/{id}`, `GET /api/v1/prompt-requests`), Pydantic v2 doğrulama şemaları (`PromptRequestCreate`, `PromptRequestRead`, `PromptRequestListResponse`), `PromptRequestRepository` ve `PromptRequestService` (`app/modules/prompts/`)
- Yapılandırılmış UTC JSON loglama (`app/core/logging.py`), sunucu taraflı `X-Request-ID` izleme ve `MAX_REQUEST_BODY_BYTES` HTTP gövde boyutu denetimi (`app/api/middleware.py`) ile merkezi RFC 9457 Problem Details (`application/problem+json`) hata yönetimi (`app/core/errors.py`, `app/api/error_handlers.py`)
- Otomatik OpenAPI şeması (`/docs`, `/redoc`, `/api/v1/openapi.json` — `ProblemDetails` şeması ile uyumlu)
- Ruff, `mypy --strict` ve `pytest` doğrulama altyapısı
- React 19, Vite 8 (`/api` geliştirme proxy'si ile), TypeScript (`strict`), Tailwind CSS v4, React Router, TanStack Query, ESLint, Vitest ve MSW ile geliştirilmiş Türkçe prompt gönderim formu (`/`), URL tabanlı sunucu taraflı sayfalama, anahtar kelime araması (`q`) ve hedef AI ailesi filtresi (`target_mode`) içeren salt okunur prompt geçmişi sayfası (`/history`), mobil ve masaüstü uyumlu üst gezinme çubuğu (`Yeni Prompt`, `Prompt Geçmişi`), RFC 9457 Problem Details uyumlu tipli API istemcisi ve UUID tabanlı prompt detay sayfası (`/prompt-requests/:id`) (sahte AI işlevi içermez)
- GitHub Actions CI iş akışı (`.github/workflows/ci.yml`)

### 2.2 Planlanan İlk MVP Kapsamı (Milestone 3 – Milestone 9)

1. Ham prompt gönderimi, bağlam bilgileri ve hedef AI modu seçimi (`generic`, `openai`, `anthropic`, `google`)
2. Temel optimizasyon kontrolleri:
   - `prompt_detail_level` (`low`, `medium`, `high`)
   - `response_detail_preference` (`minimal`, `balanced`, `comprehensive`)
   - `reasoning_preference` (`auto`, `low`, `medium`, `high`) ve görev odaklı temel çıktı politikaları
3. `PromptEngineerAgent` ile niyet analizi, belirsizlik tespiti ve `CanonicalPrompt` üretimi (`create` ve `revise`)
4. Deterministik hedef derleyicileri (`GenericPromptRenderer`, `OpenAIPromptRenderer`, `AnthropicPromptRenderer`, `GeminiPromptRenderer`)
5. Bağımsız `PromptCriticAgent` ile hedef farkındalıklı kör değerlendirme
6. Kod içinde deterministik ağırlıklı puanlama (100 üzerinden) ve kalite kapıları
7. En fazla **3 değerlendirme turu** (`evaluation_round` 1..3) ile sınırlı otomatik revizyon iş akışı
8. HTTP polling (2 sn) ile çalışma durumu izleme ve süreç içi görev yürütücüsü (`InProcessTaskRunner`)
9. Sürüm geçmişi, tur bazlı değerlendirme raporu, nihai prompt gösterimi ve panoya kopyalama

### 2.3 MVP Sonrasına (Post-MVP / M10+) Ertelenen Özellikler

- Sesli girdi / Speech-to-Text (`VoiceInput` modülü)
- Kapsamlı Token Analizi motoru ve sağlayıcıya özgü token sayım API entegrasyonları
- Sağlayıcı API'lerinde doğrudan akıl yürütme (reasoning effort) parametre kontrolü
- İkinci gerçek LLM sağlayıcısı entegrasyonu
- Server-Sent Events (SSE) canlı akışı ve çalışma iptali
- Gelişmiş onay modları, kullanıcı düzenlemesi ve satır içi diff görünümü
- Çok kullanıcılı kimlik doğrulama, proje yönetimi ve analitik gösterge paneli
- Ek uzman yazılım geliştirme ajanları (Master Prompt Architect, System Architect, Backend/Frontend Dev, QA, Security vb.)

---

## 3. Teknoloji Yığını

| Katman | Teknoloji | Durum | Seçim Gerekçesi |
|---|---|---|---|
| **Frontend** | React 19, Vite 8, TypeScript (`strict`), Tailwind CSS v4 | M2A'da kuruldu | Hızlı geliştirme döngüsü, güçlü tip güvenliği, resmi Vite eklentisiyle sade CSS yapılandırması |
| **Frontend Veri & Durum** | TanStack Query, React Router, HTTP Polling | M4A/M4B-1 sorgu, önbellek ve URL sayfalama entegrasyonu kuruldu; 2 sn çalışma polling M6'da eklenecek | Sunucu durumu önbellekleme, URL sayfa senkronizasyonu ve 2 sn aralıklı polling için ek altyapı gerektirmez |
| **Backend** | Python 3.12, FastAPI, Pydantic v2, `uv` | M2A'da kuruldu | Async I/O uyumu, katı veri şeması doğrulaması, hızlı ve kilit dosyalı (`uv.lock`) bağımlılık yönetimi |
| **Veritabanı** | SQLite (MVP) → PostgreSQL (Gelecek), SQLAlchemy Async + Alembic | M5E-2 ile beş ORM tablosu ve sürümlü Evaluation JSON şeması hazır; workflow yazma servisleri henüz etkin değil | Yerel MVP'de sıfır kurulum yükü; PostgreSQL uyumlu tipler ve kısmi indeks tanımları |
| **LLM Katmanı** | Sağlayıcıdan bağımsız `LLMProvider` protokolü + `FakeLLMProvider` + 1 gerçek sağlayıcı | Planlandı (M5) | Ağır orkestrasyon çerçeveleri olmadan deterministik test edilebilirlik ve model esnekliği |
| **Kalite & CI** | Ruff, mypy (`strict`), pytest, ESLint, Vitest, GitHub Actions | M2A'da kuruldu | Her değişiklikte otomatik statik analiz ve birim testi güvencesi |

---

## 4. Genel Sistem Mimarisi

PromptForge AI, katman sınırları net bir **modüler monolit** olarak tasarlanmıştır (`ADR-001`).

### 4.1 Mimari Diyagram

```mermaid
flowchart TD
    subgraph FE["Frontend (React + Vite + TypeScript + Tailwind)"]
        UI["Uygulama Kabugu (M2A Uygulandi)"]
        PAGES["Optimizasyon Sayfalari ve Hedef Secici (Planlandi: M4-M8.6)"]
        POLL["TanStack Query ve 2 sn Polling (Planlandi: M4-M6)"]
    end

    subgraph BE["Backend - FastAPI Moduler Monolit"]
        HEALTH["GET /api/v1/health (M2A Uygulandi)"]
        API["REST API v1 Uclari (M3C-1 prompt-requests Uygulandi; runs M6-M8.6 Planlandi)"]
        SVC["Uygulama Servisleri (M3C-1 PromptRequestService Uygulandi)"]
        RUNNER["InProcessTaskRunner (Planlandi: M6)"]
        WF["RevisionWorkflow - Maks 3 Degerlendirme Turu (Planlandi: M6-M8)"]
        COMP["GenericPromptRenderer (M5B); Hedef Renderers Planlandi"]
        SCORE["Deterministik ScoringEngine (M5C-2 Uygulandi)"]

        subgraph AGENTS["Ajan Katmani (M5B/M5C-2; workflow entegrasyonu planlandi)"]
            PE["PromptArchitectAgent (create; revise planlandi)"]
            PC["PromptCriticAgent (evaluate)"]
        end

        subgraph LLMLAYER["LLM Katmani (M5A Uygulandi)"]
            GW["LLMGateway (timeout, retry, structured output)"]
            PROV1["Gercek LLM Saglayici Adaptoru"]
            FAKE["FakeLLMProvider (deterministik testler)"]
        end

        REPO["SQLAlchemy Async Repositories (M3C-1 PromptRequestRepository Uygulandi)"]
    end

    DB[("SQLite - MVP (M5E-2: 5 ORM Tablosu ve Alembic Semasi)")]
    PG[("PostgreSQL (Post-MVP)")]
    EXT["Harici LLM API"]

    UI --> HEALTH
    PAGES --> POLL
    POLL --> API
    API --> SVC
    SVC --> REPO
    SVC --> RUNNER
    RUNNER --> WF
    WF --> PE
    WF --> COMP
    WF --> PC
    WF --> SCORE
    WF --> REPO
    PE --> GW
    PC --> GW
    GW --> PROV1
    GW -.-> FAKE
    PROV1 --> EXT
    REPO --> DB
    REPO -.-> PG
```

### 4.2 Katman Kuralları

- **Arabulucu (Mediator) ilkesi:** Ajanlar veritabanını, HTTP katmanını veya birbirlerini doğrudan çağıramaz; tüm koordinasyon `RevisionWorkflow` üzerinden yürütülür.
- **Sağlayıcı yalıtımı:** Harici SDK tipleri `llm/providers/` dışına sızmaz; ajanlar yalnızca `LLMGateway` ile konuşur.
- **Geliştirme ortamı ayrımı:** Geliştirme sırasında kullanılan IDE veya yardımcı yapay zekâ araçları, çalışma zamanındaki `LLMProvider` katmanından tamamen bağımsızdır.

### 4.3 Prompt Yönetimi API, Şema, Depo ve Servis Mimarisi (Milestone 3C-1 — Uygulandı)

- **Uç noktalar (`app/api/v1/routes/prompt_requests.py`):**
  - `POST /api/v1/prompt-requests` (`201 Created`, `Location: /api/v1/prompt-requests/{id}` başlığı ile)
  - `GET /api/v1/prompt-requests/{id}` (`200 OK`, kayıt yoksa iç veritabanı detayı sızdırmadan `404 Not Found`, geçersiz UUID için `422`)
  - `GET /api/v1/prompt-requests` (`200 OK`, `page >= 1`, `1 <= page_size <= 100`, varsayılan `page=1`, `page_size=20`)
- **Pydantic v2 şemaları ve doğrulama kuralları (`app/modules/prompts/schemas.py`):**
  - `PromptRequestCreate` (`extra="forbid"`): Bilinmeyen alanları reddeder; `raw_prompt` için boş/yalnızca-boşluk olmama ve `1..20.000` karakter sınırını zorunlu tutar; `user_context` (`<= 20.000`), `output_language` (`<= 32`) ve `task_profile` (`<= 64`) alanlarını doğrular; `app/models/enums.py` üzerindeki enum ve ORM varsayılanlarını (`generic`, `medium`, `balanced`, `auto`) tek doğruluk kaynağı olarak kullanır.
  - `PromptRequestRead` (`from_attributes=True`) ve `PromptRequestListResponse` (`items`, `total`, `page`, `page_size`) çıktı şemalarını tanımlar.
- **Depo ve servis ayrımı:**
  - `PromptRequestRepository` (`app/modules/prompts/repository.py`): FastAPI'den bağımsız olarak `AsyncSession` üzerinde parametreli SQLAlchemy 2.0 sorguları çalıştırır; sayfalama sorgularında tüm tabloyu belleğe almadan `OFFSET`/`LIMIT` ve deterministik `ORDER BY created_at DESC, id DESC` sıralaması uygular.
  - `PromptRequestService` (`app/modules/prompts/service.py`): İşlem (`commit` / hata anında açık `rollback`) sınırlarını yönetir ve bulunamayan kayıtlar için `PromptRequestNotFoundError` üretir.
- **Veritabanı ön koşulu:** FastAPI açılışında otomatik göç çalıştırılmaz; yerel veritabanı ilk kullanımdan önce açıkça `uv run alembic upgrade head` komutuyla hazırlanır.

### 4.4 Yapılandırılmış Loglama, İstek İzleme, Problem Details ve HTTP Güvenliği (Milestone 3C-2 — Uygulandı)

- **Yapılandırılmış JSON Loglama (`app/core/logging.py`):** Standart Python `logging` modülü ile `stdout` üzerine UTC ISO-8601 zaman damgalı tek satırlık JSON logları (`timestamp`, `level`, `logger`, `message`, `request_id` ve bağlama göre `method`, `path`, `status_code`, `duration_ms`, `error_code`, `workflow_run_id`, `agent_name`, `exception_type`) üretilir. `LOG_LEVEL` (`DEBUG`..`CRITICAL`, varsayılan `INFO`) ile yapılandırılır ve çoklu `create_app()` çağrılarında mükerrer handler biriktirmez. `raw_prompt`, `user_context`, istek/yanıt gövdeleri, ham sorgu dizeleri, API anahtarları, erişim belirteçleri ve veritabanı kimlik bilgileri asla loglanmaz veya `[REDACTED]` olarak maskelenir.
- **İstek İzleme (`RequestTracingMiddleware` — `app/api/middleware.py`):** Her HTTP isteğine sunucu tarafında üretilen benzersiz `uuid4` kimliği atanır (`ContextVar` + `scope["state"]["request_id"]`), tüm başarılı ve hatalı yanıtlarda (`500` dahil) `X-Request-ID` başlığı ile döndürülür ve istek sonunda bağlam temizlenir.
- **Merkezi Hata Yönetimi ve RFC 9457 Problem Details (`app/core/errors.py`, `app/api/error_handlers.py`):** `AppError` hiyerarşisi (`BadRequestError` [400], `NotFoundError` [404] -> `PromptRequestNotFoundError`, `RequestBodyTooLargeError` [413], `DomainValidationError` [422], `InternalApplicationError` [500]), FastAPI `RequestValidationError` (422), `HTTPException` ve beklenmeyen uygulama/SQLAlchemy istisnaları merkezi olarak `application/problem+json` (`ProblemDetails`) formatına dönüştürülür. `422` doğrulama hataları ham kullanıcı girdisi veya prompt metni sızdırmadan `errors: [{ "field", "code", "message" }]` dizisini döner; `500` hataları yığın izi, SQL veya bağlantı bilgisi sızdırmaz. OpenAPI şeması hata yanıtlarında doğrudan `ProblemDetails` modelini yayınlar.
- **HTTP Gövde Boyutu Sınırı (`RequestBodySizeLimitMiddleware` — `app/api/middleware.py`):** `POST`, `PUT` ve `PATCH` isteklerinde hem `Content-Length` başlığını hem de akışlı (`chunked`) bayt sayısını denetleyerek `MAX_REQUEST_BODY_BYTES` (varsayılan `262144` bayt / 256 KiB; çok baytlı UTF-8 `20.000` karakter `raw_prompt` + `20.000` karakter `user_context` + JSON üst verisi ile tam uyumlu) sınırını aşan istekleri belleği şişirmeden `413 Content Too Large` Problem Details yanıtıyla reddeder.

---

## 5. CanonicalPrompt ve Hedefe Özgü Prompt Derleyicisi (Prompt Compiler)

### 5.1 Desteklenen Hedef Modlar (`ADR-011`)

Kullanıcı prompt üretimi öncesinde dört hedef moddan birini seçebilir:

| Teknik Değer (`target_mode`) | UI Gösterimi | Planlanan Renderer Modülü | Milestone |
|---|---|---|---|
| `generic` | **Universal** | `GenericPromptRenderer` | M6 |
| `openai` | **ChatGPT / GPT** | `OpenAIPromptRenderer` | M8.5 |
| `anthropic` | **Claude** | `AnthropicPromptRenderer` | M8.5 |
| `google` | **Gemini** | `GeminiPromptRenderer` | M8.5 |

### 5.2 `CanonicalPrompt` Ara Temsili

`PromptEngineerAgent`, serbest metin yerine yapılandırılmış `CanonicalPrompt` şemasını üretir:

- `role`: Uzman rol ve sorumluluk tanımı
- `context`: Arka plan ve çalışma ortamı bağlamı
- `input_data`: Modele verilecek veri veya girdi açıklamaları
- `task`: Birincil görev tanımı
- `requirements`: Karşılanması zorunlu açık gereksinimler
- `constraints`: Biçim, kapsam ve kalite kısıtları
- `prohibitions`: Yapılmaması gereken davranışlar (negatif kurallar)
- `output_format`: Beklenen çıktı yapısı
- `examples`: Doğrulanmış girdi/çıktı örnekleri (varsa yalnızca `PromptEngineerAgent` tarafından üretilir)
- `variables`: Şablon değişkenleri listesi
- `assumptions`: Belirsizlikler için açıkça beyan edilen varsayımlar

### 5.3 Renderer Kuralları ve Değerlendirme Bütünlüğü

1. **İçerik uydurmama kuralı:** Renderer modülleri saf (pure), deterministik ve sürümlü (`renderer_version`) fonksiyonlardır. Yeni gereksinim uyduramaz, önemli bilgileri silemez ve doğrulanmamış few-shot örnekleri üretemez.
2. **Hedefe özgü içerik sorumluluğu:** Seçilen hedefe uygun içerik kararları (ör. örnek üretimi veya ek negatif kısıtlar) `PromptEngineerAgent` tarafından üretilir ve `PromptCriticAgent` tarafından denetlenir.
3. **Kanıta dayalı yaklaşım:** XML etiketlerinin, Markdown başlıklarının veya few-shot örneklerinin her senaryoda otomatik üstünlük sağladığı varsayılmaz; sağlayıcı rehberlerindeki öneriler sürümlü kurallar olarak uygulanır ve değerlendirme testleriyle doğrulanır.
4. **Hedef farkındalıklı değerlendirme:** Teslim edilen nihai derlenmiş metin, bizzat `PromptCriticAgent` tarafından değerlendirilmiş sürümdür. Her değerlendirme kaydı `target_mode` ve `renderer_version` bilgisini saklar.
5. **Değerlendirme sonrası hedef değişimi:** Kullanıcı üretim tamamlandıktan sonra yeni bir değerlendirme çalıştırmadan farklı bir hedef sekmeye geçerse, arayüz bu varyantı **"bağımsız olarak değerlendirilmedi (yalnızca biçimsel yeniden derleme)"** uyarısıyla işaretler. Genel (`generic`) değerlendirme sonucunun tüm hedef modellerde aynı kaliteyi kanıtladığı iddia edilmez.

---

## 6. Optimizasyon Kontrolleri ve Bağlam Duyarlı Politikalar (`ADR-012`)

### 6.1 Üç Yapılandırma Boyutu (Milestone 8.6)

| Boyut | Değerler | İşlevi ve Sınırları |
|---|---|---|
| **A. Prompt Detail Level** (`prompt_detail_level`) | `low`, `medium`, `high` | Üretilen promptun ayrıntı düzeyini belirler. `low` seçilse dahi kullanıcının açık gereksinimleri ve temel doğruluk kısıtları **asla çıkarılmaz**. |
| **B. Response Detail Preference** (`response_detail_preference`) | `minimal`, `balanced`, `comprehensive` | Hedef modelden beklenen yanıtın kapsam ve uzunluk tercihini prompt içinde açıkça tanımlar. |
| **C. Reasoning Preference** (`reasoning_preference`) | `auto`, `low`, `medium`, `high` | İstenen akıl yürütme derinliğini temsil eder. |

> **Kritik Ayrım (Prompt İçi Yönerge vs. API Parametresi):** Kullanıcının kopyalayıp harici bir yapay zekâ arayüzüne yapıştırdığı promptta yer alan akıl yürütme tercihi yalnızca **metinsel bir yönlendirmedir**; karşı uygulamanın gerçek API `reasoning_effort` / thinking bütçesi ayarını değiştireceği garanti edilemez. Gerçek sağlayıcı API akıl yürütme kontrolü yalnızca PromptForge AI'ın uyumlu bir sağlayıcı API'sini doğrudan çağırdığı senaryolarda (Post-MVP) uygulanabilir. Sabit oranlı token tasarrufu veya garanti performans artışı vaat edilmez.

### 6.2 Bağlam Duyarlı Çıktı Politikaları

Yazılım geliştirme görev türlerine göre yapılandırılabilir varsayılan politikalar tanımlanır (kullanıcı her zaman geçersiz kılabilir):

- **Mimari tasarım (`architecture_design`):** Bütünlük, güvenlik ve ödünleşim (trade-off) analizini önceliklendirir.
- **Yeni proje geliştirme (`new_project`):** Gerekli dosya yapısını ve uygulama ayrıntılarını tam ister.
- **Hata ayıklama (`debugging`):** Hedefli yama (patch), kök neden ve ilgili testlere odaklanır.
- **Küçük değişiklikler (`minor_modification`):** İlgisiz dosyaların baştan yazılmasını engeller.
- **Yeniden düzenleme (`refactoring`):** Davranış korunumunu ve gerekli değişiklik kapsamını vurgular.
- **Güvenlik incelemesi (`security_review`):** Token azaltmak amacıyla hiçbir önemli güvenlik bulgusunun atlanmamasını şart koşar.

---

## 7. Ajan Sorumlulukları, Değerlendirme ve Revizyon İş Akışı

### 7.1 Ajan Sorumlulukları

- **`PromptArchitectAgent` (önceki plan adı `PromptEngineerAgent`):** M5B'de `create` için preserve/rewrite/clarify ve sparse canonical çıktı uygulandı. M5D-2'de [desteklenen Critic bulgularıyla tek revizyon](architect-revision.md) eklendi; baseline koruma ile ilk raw_prompt koruma farklıdır. M5D-3 [saf tur/seçim/ilerleme politikalarını](optimization-policies.md), M5D-4 ise [sınırlı bellek içi optimizasyon yürütücüsünü](prompt-optimizer.md) uygular; arka plan çalıştırma, API aktivasyonu ve kalıcılık halen planlanmaktadır.
- **`PromptCriticAgent`:** M5C-2'de orijinal istek ve kesin derlenmiş aday rubric 2.0.0 ile **kör** değerlendirilir. Yapılandırılmış kriter puanları/null, desteklenen veya belirsiz sorunlar, kanıtlar ve uygulanabilir öneriler döner; toplam ve onay Python ScoringEngine sorumluluğundadır. M5D-4 bellek içi yürütücü bu sınırı kullanır; kalıcı değerlendirme, arka plan ve API entegrasyonu planlanmaktadır.

### 7.2 Değerlendirme Kuralları (Production rubric 2.0.0 — M5C-2)

Toplam puan LLM tarafından değil, `ScoringEngine` tarafından deterministik olarak hesaplanır:

$$
\text{total\_score} = \sum_{i=1}^{9} \frac{s_i}{10} \times w_i \qquad (s_i \in [0, 10],\ \sum w_i = 100)
$$

- **Kriter ağırlıkları:** `intent_fidelity` (25), `clarity` (10), `completeness` (15), `structure` (5), `context_and_role` (5), `constraints_and_scope` (20), `output_format` (5), `specificity` (10), `consistency` (5).
- **Kalite kapıları (`passed = true` koşulları):** `total_score >= 85`, `critical` sorun sayısı `0` ve `intent_fidelity >= 7`.
- Dokuz kriterden biri değerlendirilemiyorsa toplam null, durum `unassessable` ve `passed=false` olur; ağırlıklar yeniden normalize edilmez. Kanıt atfı yerel doğrulanır, desteklenmeyen kesinlik kaldırılır. Önceki rubric v1 ağırlıkları M5C-2'de değiştirilmiştir; ayrıntılar ve sekiz insan değerlendirme kriteriyle ilişki [ADR-010](adr/ADR-010-independent-critic-and-deterministic-scoring.md) ve [Critic dokümanında](prompt-critic.md) açıklanır. Puanlar gerçek dünya başarısının kanıtı değildir.

### 7.3 Kesin Değerlendirme Turu Tanımı (`evaluation_round` 1..3)

Her optimizasyon çalıştırması (`workflow_run`) en fazla **3 değerlendirme turu** içerir (`ADR-003`):

- **Değerlendirme 1 (`evaluation_round = 1`):** İlk prompt üretimi (`create`), hedef derlemesi ve 1. Critic değerlendirmesi.
- **Değerlendirme 2 (`evaluation_round = 2`):** Kalite kapıları geçilmediyse 1. revizyon (`revise`), derleme ve 2. Critic değerlendirmesi.
- **Değerlendirme 3 (`evaluation_round = 3`):** Kalite kapıları hâlâ geçilmediyse 2. revizyon (`revise`), derleme ve 3. (son) Critic değerlendirmesi. **Sistem 3. değerlendirmeden sonra koşulsuz durur.**

Geçici ağ hataları için yapılan yeniden denemeler (`attempt`) ve şema onarım adımları tur sayısını artırmaz. Revizyon adımı her zaman o ana kadarki **en yüksek nitelikli değerlendirilmiş sürümü** temel alır (`ADR-007`).

### 7.4 Ajan Etkileşim Sıra Diyagramı

The following API/background/database sequence remains a future integration plan.
M5D-4 implements only the opt-in in-memory execution described in
[the optimizer contract](prompt-optimizer.md), without those records/endpoints.

```mermaid
sequenceDiagram
    autonumber
    actor U as Kullanici
    participant FE as Frontend
    participant API as FastAPI
    participant WF as RevisionWorkflow
    participant PE as PromptEngineerAgent
    participant CR as PromptRenderer
    participant PC as PromptCriticAgent
    participant SC as ScoringEngine
    participant DB as Database

    U->>FE: Ham prompt, target_mode ve tercihleri gonderir
    FE->>API: POST /api/v1/prompt-requests
    API->>DB: prompt_request kaydet
    API-->>FE: 201 Created
    FE->>API: POST /api/v1/prompt-requests/{id}/runs
    API->>DB: workflow_run olustur (PENDING)
    API->>WF: InProcessTaskRunner ile baslat
    API-->>FE: 202 Accepted (run_id)

    par Arka plan revizyon dongusu
        WF->>PE: create(raw_prompt, target_mode, preferences)
        PE-->>WF: CanonicalPrompt v1
        WF->>CR: render(CanonicalPrompt v1, target_mode)
        CR-->>WF: rendered_prompt v1
        WF->>DB: prompt_version (round 1) kaydet
        WF->>PC: evaluate(raw_prompt, rendered_prompt v1, target_mode)
        PC-->>WF: CriticOutput
        WF->>SC: score(criterion_scores, issues)
        SC-->>WF: total_score, passed
        WF->>DB: evaluation (round 1) kaydet

        loop Round 2 ve Round 3 (yalnizca onceki tur gecmediyse)
            WF->>PE: revise(raw_prompt, best_version, critic_feedback)
            PE-->>WF: CanonicalPrompt vN
            WF->>CR: render(CanonicalPrompt vN, target_mode)
            CR-->>WF: rendered_prompt vN
            WF->>DB: prompt_version (round N) kaydet
            WF->>PC: evaluate(raw_prompt, rendered_prompt vN, target_mode)
            PC-->>WF: CriticOutput
            WF->>SC: score(criterion_scores, issues)
            SC-->>WF: total_score, passed
            WF->>DB: evaluation (round N) kaydet
        end

        WF->>WF: select_best_version()
        WF->>DB: workflow_run COMPLETED
    and Istemci durum sorgulama
        loop Terminal duruma kadar her 2 saniyede bir
            FE->>API: GET /api/v1/runs/{run_id}
            API-->>FE: status, current_round, completed_rounds
        end
    end

    FE->>API: GET /api/v1/runs/{run_id}/versions
    API-->>FE: Surum gecmisi, degerlendirme raporu ve final prompt
```

### 7.5 Durum Makinesi Diyagramı

**M5D-4 amendment:** the earlier `COMPLETED/error_partial` failure transitions are
superseded by failed execution with retained completed history/best selection
([ADR-005 amendment](adr/ADR-005-in-process-task-runner.md)). The diagram below
shows that corrected failure policy; persisted execution remains planned.
Clarification is a separate in-memory outcome; no database waiting state is added.

```mermaid
stateDiagram-v2
    state "PENDING" as PENDING
    state "Round 1 - GENERATING" as R1_GEN
    state "Round 1 - EVALUATING" as R1_EVAL
    state "Round 2 - REVISING" as R2_REV
    state "Round 2 - EVALUATING" as R2_EVAL
    state "Round 3 - REVISING" as R3_REV
    state "Round 3 - EVALUATING" as R3_EVAL
    state "COMPLETED" as COMPLETED
    state "FAILED" as FAILED

    [*] --> PENDING
    PENDING --> R1_GEN: run baslatildi
    R1_GEN --> R1_EVAL: surum 1 derlendi
    R1_EVAL --> COMPLETED: gecti veya tur siniri 1
    R1_EVAL --> R2_REV: gecmedi
    R2_REV --> R2_EVAL: surum 2 derlendi
    R2_EVAL --> COMPLETED: gecti veya tur siniri 2
    R2_EVAL --> R3_REV: gecmedi
    R3_REV --> R3_EVAL: surum 3 derlendi
    R3_EVAL --> COMPLETED: 3. degerlendirme tamamlandi (kesin durus)

    R1_GEN --> FAILED: kalici hata
    R1_EVAL --> FAILED: kalici hata
    R2_REV --> FAILED: hata (v1 korunur)
    R2_EVAL --> FAILED: hata (v1 korunur)
    R3_REV --> FAILED: hata (en iyi surum korunur)
    R3_EVAL --> FAILED: hata (en iyi surum korunur)

    COMPLETED --> [*]
    FAILED --> [*]
```

---

## 8. Veritabanı Şeması ve Göç Mimarisi (M5E-2)

Asenkron bağlantı ve oturum altyapısı M3A'da, ilk dört ORM tablosu ve `0001_initial_schema` M3B'de uygulanmıştır. M5E-2, `0002_workflow_persistence` ile yalnızca şema ve saf Pydantic saklama projeksiyonlarını ekler:

- **Mevcut temel tablolar:** `prompt_requests`, `workflow_runs`, `prompt_versions`, `agent_runs`.
- **Yeni uygulanan tablo:** `evaluations`; tam Critic değerlendirmesi ve kapılar açık sürümlü JSON içinde tutulur. Önceki `criterion_scores` / `evaluation_issues` alt tablo önerisi bu MVP için geçersizdir.
- **Henüz uygulanmayan:** Workflow repository/servisleri, optimizer kalıcılık bağlantısı, recovery ve yeni uç noktalar. Mevcut PromptRequest API ve bellek içi optimizer değişmedi. Geliştirme veritabanına göç uygulanmadı.
- Alanların, sürümlerin, ertelenen çapraz kayıt doğrulamalarının ve SQLite çalışma zamanı önkoşullarının ayrıntıları: [M5E-2 şema sözleşmesi](workflow-persistence-schema.md).

### 8.1 Göç (Migration) ve Bütünlük Kuralları

- **Alembic ve `DATABASE_URL` entegrasyonu:** `migrations/env.py`, `Settings().database_url` ve `create_db_engine()` altyapısını yeniden kullanır; `create_all()` kullanılmaz ve FastAPI başlangıcında otomatik göç çalıştırılmaz. Göçler `alembic.ini` (`%(here)s`) ve `build_alembic_config()` sayesinde çalışma dizininden (CWD) bağımsız olarak `uv run alembic upgrade head` ve `uv run alembic downgrade -1` / `base` komutlarıyla yürütülür.
- **Döngüsel yabancı anahtar çözümü (`WorkflowRun.final_version_id` ↔ `PromptVersion.id`):** `workflow_runs` ilk oluşturulduğunda `final_version_id` sütunu FK olmadan tanımlanır; `prompt_versions` tablosu oluşturulduktan sonra `op.batch_alter_table("workflow_runs")` (`use_alter=True`, `post_update=True`) ile `fk_workflow_runs_final_version_id_prompt_versions` (`ON DELETE SET NULL`) kısıtı eklenir. Geri alma (`downgrade`) sırasında `PRAGMA foreign_keys=ON` küresel olarak devre dışı bırakılmaz; önce `final_version_id` değerleri `NULL` yapılır, PostgreSQL gibi katalog tabanlı lehçelerde döngüsel FK düşürülür ve tablolar bağımlılık sırasıyla kaldırılır.
- **Tarihsel kayıtların korunması ve kısıtlar:** Üretim ve yürütme geçmişinin yanlışlıkla silinmemesi için ebeveyn-çocuk ilişkilerinde `ON DELETE RESTRICT` uygulanır (`CASCADE` kullanılmaz). Üç değerlendirme turu sınırı (`max_evaluation_rounds BETWEEN 1 AND 3`, `evaluation_round BETWEEN 1 AND 3`) ve sürüm tekilliği (`UNIQUE(prompt_request_id, version_number)`, `UNIQUE(agent_run_id)`) veritabanı düzeyinde zorunlu tutulur.
- **M5E-2 SQLite göçü:** Dolu ve döngüsel başvurulu tabloların batch yeniden kurulumu için yalnızca özel göç bağlantısında, işlem dışında FK denetimi geçici kapatılır. Açık `BEGIN IMMEDIATE`, DDL ve Alembic damgasını tek işlemde tutar; commit öncesi `foreign_key_check` çalışır ve bağlantının ilk FK ayarı her durumda geri yüklenir. Uygulamanın bağlantı ayarları değişmez. Dolu yeni alanlar veya Evaluation geçmişi varsa 0001'e downgrade veri silmek yerine reddedilir.
- **M5E-2 sahiplik ve yaşam döngüsü:** Evaluation'ın PromptVersion ve Critic AgentRun bağlantıları bileşik FK ile aynı workflow'a bağlanır. Dört etkin durumda (`PENDING`, `GENERATING`, `EVALUATING`, `REVISING`) istek başına tek workflow kısmi benzersiz indeksle korunur; terminal `CLARIFICATION_REQUIRED` yeni koşuya engel olmaz. Seçim, baseline sırası ve diğer çapraz kayıt kuralları M5E-3 servis işlemlerine ayrılmıştır.

### 8.2 Uygulanan Varlık-İlişki Diyagramı (M5E-2)

```mermaid
erDiagram
    PROMPT_REQUESTS ||--o{ WORKFLOW_RUNS : has
    PROMPT_REQUESTS ||--o{ PROMPT_VERSIONS : has
    WORKFLOW_RUNS |o--o{ PROMPT_VERSIONS : produces
    WORKFLOW_RUNS ||--o{ EVALUATIONS : contains
    WORKFLOW_RUNS ||--o{ AGENT_RUNS : records
    WORKFLOW_RUNS }o--o| PROMPT_VERSIONS : selects_final
    PROMPT_VERSIONS ||--o{ EVALUATIONS : evaluated_by
    PROMPT_VERSIONS |o--o{ PROMPT_VERSIONS : parent_of
    EVALUATIONS |o--o{ AGENT_RUNS : revision_baseline
    AGENT_RUNS ||--o| PROMPT_VERSIONS : creates
    AGENT_RUNS ||--o| EVALUATIONS : creates

    PROMPT_REQUESTS {
        uuid id PK
        text raw_prompt
        text user_context
        string output_language
        string target_mode "generic, openai, anthropic, google"
        string task_profile
        string prompt_detail_level "low, medium, high"
        string response_detail_preference "minimal, balanced, comprehensive"
        string reasoning_preference "auto, low, medium, high"
        datetime created_at
    }
    WORKFLOW_RUNS {
        uuid id PK
        uuid prompt_request_id FK
        string status
        int max_evaluation_rounds "CHECK 1..3"
        int current_round
        int completed_rounds
        json config_snapshot
        json request_snapshot "preferences/source identity only"
        string request_content_digest
        int state_revision "default 0"
        uuid execution_token
        datetime lease_expires_at
        string client_idempotency_key_digest
        json terminal_details
        uuid final_version_id FK
        decimal final_score
        bool qualified
        string stop_reason
        string error_code
        text error_message
        datetime created_at
        datetime started_at
        datetime finished_at
    }
    PROMPT_VERSIONS {
        uuid id PK
        uuid prompt_request_id FK
        uuid workflow_run_id FK
        uuid parent_version_id FK
        uuid agent_run_id FK
        int version_number
        int evaluation_round "CHECK 1..3"
        string source
        string target_mode
        string renderer_name
        string renderer_version
        json canonical_prompt
        text rendered_prompt
        json analysis
        json change_summary
        datetime created_at
    }
    EVALUATIONS {
        uuid id PK
        uuid prompt_version_id FK
        uuid workflow_run_id FK
        uuid critic_agent_run_id FK
        int evaluation_round "CHECK 1..3"
        string input_fingerprint
        string evaluated_target_mode
        string evaluated_renderer_name
        string evaluated_renderer_version
        string rubric_version
        string scoring_version
        string assessment_schema_version
        json assessment "nine criteria and issues"
        string quality_status
        decimal total_score "nullable"
        bool passed
        json gate_results
        datetime completed_at
        datetime created_at
    }
    AGENT_RUNS {
        uuid id PK
        uuid workflow_run_id FK
        string agent_name
        string agent_version
        string instruction_version
        string operation
        string operation_key
        string revision_attempt_key
        uuid baseline_evaluation_id FK
        string result_kind
        string error_code
        json binding_data
        json result_data
        string feedback_projection_digest
        json call_metadata
        int evaluation_round
        string provider "nullable configured identity"
        string model "nullable configured identity"
        json parameters
        string status
        int attempt_count "nullable when unknown"
        bool repair_attempted "nullable when unknown"
        int input_tokens
        int output_tokens
        int latency_ms
        text error_message
        datetime started_at
        datetime finished_at
    }
```

---

## 9. Planlanan Post-MVP Mimarileri

### 9.1 Token Analizi Mimarisi (`ADR-013` — Post-MVP)

Gelecekteki `TokenAnalysis` modülü, iç yürütme maliyeti ile üretilen promptun dış kullanım tüketimini kesin biçimde ayırır:

1. **Prompt girdi tokenları (`prompt_input_tokens`):** Derlenen promptun hedef modelde kaplayacağı girdi boyutu (desteklenen sağlayıcılarda resmi token sayım API'leriyle, diğer durumlarda açıkça **"yaklaşık tahmin (approximation)"** etiketi taşıyan yerel tahminleyicilerle hesaplanır).
2. **Beklenen görünür çıktı uzunluğu (`expected_visible_output_range`):** Yanıt detay tercihine göre yaklaşık aralık; kesin tahmin olarak sunulmaz.
3. **Akıl yürütme tokenları (`reasoning_tokens`):** Yalnızca doğrudan çağrılan sağlayıcı API'si raporladığında gösterilir.
4. **Gerçek API token kullanımı (`actual_api_usage`):** Doğrudan API çağrılarından dönen kesin sayaçlar.
5. **Tahmini aşağı akış kullanımı (`estimated_downstream_usage`):** Prompt başka bir uygulamaya kopyalandığında oluşabilecek kaba aralık tahmini (kesinlik iddiası taşımaz; "%60 garanti tasarruf" gibi desteksiz ifadeler kullanılmaz).
6. **PromptForge iç ajan tüketimi (`internal_agent_execution_usage`):** Engineer ve Critic çalıştırmalarının `agent_runs` tablosunda tutulan gerçek iç tüketimi.

### 9.2 Gelecek Sesli Girdi / Speech-to-Text Mimarisi (`ADR-014` — Post-MVP)

Gelecekteki `VoiceInput` modülü aşağıdaki ilkelerle planlanmıştır:

- Tarayıcı mikrofon izin yönetimi, hata durumları ve kayıt kontrolü
- Değiştirilebilir `TranscriptionProvider` arayüzü (ilk aşamada özel bir transkripsiyon API'si ile)
- Türkçe, İngilizce ve Türkçe/İngilizce karışık yazılım terminolojisi desteği
- **Düzenlenebilir transkripsiyon ve zorunlu kullanıcı onayı:** Ses metne çevrildikten sonra doğrudan ajan döngüsüne sokulmaz; kullanıcı metni inceler, düzenler ve açıkça onayladıktan sonra optimizasyon başlar.
- **Gizlilik odaklı ses işleme:** Ham ses kayıtları varsayılan olarak kalıcı veritabanında saklanmaz, yalnızca transkripsiyon süresince bellekte/geçici akışta işlenir.

---

## 10. Geliştirme Yol Haritası

```mermaid
flowchart LR
    subgraph MVP["MVP Yol Haritasi (v0.1.0)"]
        M1["M1 Mimari (Tamamlandi)"]
        M2A["M2A Temel Kurulum (Tamamlandi)"]
        M3["M3 Backend ve DB Temeli"]
        M4["M4 Frontend Temeli"]
        M5["M5 LLM Katmani"]
        M6["M6 Engineer, CanonicalPrompt, GenericRenderer"]
        M7["M7 Critic ve Deterministik Skorlama"]
        M8["M8 3 Turlu Revizyon Workflow"]
        M85["M8.5 Hedef Renderers ve Hedef Secici"]
        M86["M8.6 Detay, Yanit ve Reasoning Kontrolleri"]
        M9["M9 MVP Dogrulama ve Surum"]
    end

    subgraph POST["Post-MVP (M10+)"]
        P1["Sesli Girdi (Speech-to-Text)"]
        P2["Ek LLM Saglayicilari ve Reasoning API"]
        P3["Token Sayim ve Analiz Motoru"]
        P4["SSE, Projeler, Auth ve Yeni Ajanlar"]
    end

    M1 --> M2A
    M2A --> M3
    M3 --> M4
    M3 --> M5
    M4 --> M6
    M5 --> M6
    M6 --> M7
    M7 --> M8
    M8 --> M85
    M85 --> M86
    M86 --> M9
    M9 --> P1
    M9 --> P2
    M9 --> P3
    M9 --> P4
```

| Milestone | Kapsam Özeti | Durum |
|---|---|---|
| **M1** | Mimari tasarım ve kararların onaylanması | Tamamlandı |
| **M2A** | Monorepo iskeleti, FastAPI `/api/v1/health`, React + Tailwind başlangıç kabuğu, test/lint/CI, mimari ve ADR dokümanları | Tamamlandı |
| **M3A** | `pydantic-settings` ortam yapılandırması, SQLite (`backend/data/promptforge.db`) + SQLAlchemy 2.0 Async motoru, oturum yaşam döngüsü ve PostgreSQL uyumlu bağlantı katmanı | **Tamamlandı** |
| **M3B** | İlk dört ORM modeli (`prompt_requests`, `workflow_runs`, `prompt_versions`, `agent_runs`) ve asenkron Alembic göç altyapısı (`0001_initial_schema`) | **Tamamlandı** |
| **M3C-1** | Prompt yönetimi REST API uç noktaları (`POST/GET /api/v1/prompt-requests`), Pydantic v2 doğrulama şemaları, `PromptRequestRepository` ve `PromptRequestService` | **Tamamlandı** |
| **M3C-2** | Yapılandırılmış JSON loglama, `X-Request-ID` ara katmanı, HTTP istek gövde boyutu sınırı ve merkezi RFC 9457 Problem Details hata yönetimi | **Tamamlandı** |
| **M4A** | React Router, TanStack Query, Problem Details uyumlu API istemcisi, Türkçe prompt gönderim formu ve UUID tabanlı detay sayfası | **Tamamlandı** |
| **M4B-1** | Kaydedilen prompt istekleri için sunucu taraflı sayfalamalı geçmiş arayüzü (`/history`), URL `?page=` durumu ve üst gezinme çubuğu | **Tamamlandı** |
| **M4B-2** | Sunucu taraflı anahtar kelime araması, hedef AI ailesi filtreleme ve URL durum eşgüdümü | **Tamamlandı** |
| **M5** | `LLMProvider` protokolü, `LLMGateway`, `FakeLLMProvider` ve ilk gerçek LLM sağlayıcı adaptörü | M5A'da uygulandı |
| **M6** | `PromptArchitectAgent.create/revise`, sparse `CanonicalPrompt` ve `GenericPromptRenderer` | M5B/M5D-2'de uygulandı; M5D-3 politikaları ve M5D-4 bellek içi yürütücü mevcut |
| **M7** | Bağımsız `PromptCriticAgent`, rubric 2.0.0 ve deterministik `ScoringEngine` | M5C-2'de bellek içi uygulandı; değerlendirme tabloları/kalıcılık planlandı |
| **M8** | En fazla 3 değerlendirme turlu otomatik `RevisionWorkflow` ve en iyi sürüm seçimi | M5D-4 sınırlı bellek içi yürütücü uygulandı; arka plan çalıştırma, API ve kalıcılık planlandı |
| **M8.5** | `OpenAIPromptRenderer`, `AnthropicPromptRenderer`, `GeminiPromptRenderer`, hedef AI seçici ve hedef farkındalıklı değerlendirme | Planlandı (MVP Zorunlu) |
| **M8.6** | `prompt_detail_level`, `response_detail_preference`, `reasoning_preference` üst verisi ve görev bazlı temel optimizasyon politikaları | Planlandı (MVP Zorunlu) |
| **M9** | Uçtan uca doğrulama, kalite değerlendirme seti ve `v0.1.0` MVP sürümü | Planlandı |
| **Post-MVP** | Speech-to-Text, ek LLM sağlayıcıları, sağlayıcıya özgü token sayımı ve analitiği, doğrudan reasoning API kontrolleri, uzman yazılım ajanları | Planlandı |
