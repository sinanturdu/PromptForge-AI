# PromptForge AI — Backend

FastAPI tabanlı modüler monolit backend. Mimari ayrıntılar için bkz. [`../docs/architecture.md`](../docs/architecture.md), [`../docs/adr/ADR-003-three-evaluation-rounds-cap.md`](../docs/adr/ADR-003-three-evaluation-rounds-cap.md) ve [`../docs/adr/ADR-009-sqlite-to-postgresql-portability.md`](../docs/adr/ADR-009-sqlite-to-postgresql-portability.md).

## Mevcut durum (Milestone 2A, 3A, 3B & 3C-1 — Uygulandı)

- `GET /api/v1/health` uç noktası ve otomatik OpenAPI dokümantasyonu (`/docs`, `/redoc`, `/api/v1/openapi.json`)
- `pydantic-settings` tabanlı ortam yapılandırması (`app/core/config.py`)
- SQLAlchemy 2.0 Async motoru, asenkron oturum yönetimi (`DatabaseSessionManager`, `get_db_session`) ve FastAPI `lifespan` entegrasyonu (`app/db/session.py`)
- SQLAlchemy 2.0 `DeclarativeBase` (`app/db/base.py`), UTC farkındalıklı zaman damgası (`UTCDateTime`), adlandırma kuralları (`NAMING_CONVENTION`) ve ilk dört temel ORM modeli (`app/models/`)
- Asenkron uyumlu, çalışma dizininden (CWD) bağımsız Alembic veritabanı göç (migration) sistemi (`alembic.ini`, `migrations/`, `app/db/migrations.py`) ve başlangıç revizyonu (`0001_initial_schema`)
- Prompt istek yönetimi REST API uç noktaları (`POST /api/v1/prompt-requests`, `GET /api/v1/prompt-requests/{id}`, `GET /api/v1/prompt-requests`), Pydantic v2 doğrulama şemaları, `PromptRequestRepository` ve `PromptRequestService` katmanları (`app/modules/prompts/`, `app/api/v1/routes/prompt_requests.py`)
- pytest, Ruff ve `mypy --strict` doğrulama altyapısı

> **Not (Planlanan Bileşenler):** Yapılandırılmış JSON loglama, `X-Request-ID` ara katmanı ve merkezi RFC 9457 Problem Details hata yönetimi (**Milestone 3C-2**), iş akışı çalıştırma uç noktaları, `evaluations` / `criterion_scores` / `evaluation_issues` tabloları (Milestone 7), AI ajanları (`PromptEngineerAgent`, `PromptCriticAgent`) ve LLM sağlayıcı entegrasyonları **henüz uygulanmamıştır**.

---

## Veritabanı Bağlantı Temeli (Milestone 3A)

- **Gerekli bağımlılıklar:** `sqlalchemy[asyncio]>=2.0,<3.0` (`greenlet` dahil), `aiosqlite>=0.20`, `alembic>=1.14`, `pydantic-settings>=2.6`.
- **Varsayılan SQLite konumu:** Terminal çalışma dizininden (CWD) bağımsız olarak `backend/data/promptforge.db` (`sqlite+aiosqlite:///<backend_dir>/data/promptforge.db`). `backend/data/` dizini ve tüm SQLite dosyaları Git dışındadır; veritabanı dosyası modül içe aktarımında veya uygulama açılışında değil, ilk gerçek bağlantıda ya da açıkça göç çalıştırıldığında oluşturulur.
- **Ortam değişkenleri (`backend/.env`):** Şablon için `backend/.env.example` dosyasına bakınız:
  - `DATABASE_URL`: Varsayılan yerel SQLite yolunu geçersiz kılar (ör. testlerde geçici dosya URL'si veya `sqlite+aiosqlite:///:memory:`, ileride `postgresql+asyncpg://...`).
  - `DATABASE_CONNECT_TIMEOUT`: Bağlantı ve kilit bekleme zaman aşımı (saniye, varsayılan `30.0`).
  - `DATABASE_ECHO`: SQLAlchemy SQL günlükleme bayrağı (varsayılan `false`).
- **Asenkron oturum yönetimi:** `DatabaseSessionManager.session()` ve FastAPI `Depends(get_db_session)` her istek için `AsyncSession` üretir (`autoflush=False`, `expire_on_commit=False`), istisna durumunda otomatik `rollback()` uygular ve oturumu her koşulda kapatır. Uygulama kapanışında FastAPI `lifespan` üzerinden `engine.dispose()` çağrılır.
- **SQLite ve PostgreSQL taşınabilirliği:** Yalnızca SQLite lehçesi algılandığında `PRAGMA foreign_keys=ON` ve `busy_timeout` etkinleştirilir; dosya tabanlı SQLite veritabanlarında ek olarak `PRAGMA journal_mode=WAL` uygulanır. PostgreSQL gibi diğer asenkron lehçelerde SQLite kancaları devreye girmez.

---

## ORM Modelleri ve Oluşturulan Tablolar (Milestone 3B)

Tüm ORM modelleri `app/db/base.py` içindeki `Base(DeclarativeBase)` sınıfından türetilir ve lehçeden bağımsız SQLAlchemy 2.0 tipleri (`Uuid`, `JSON`, `Numeric`, `String`, `Text`, `UTCDateTime`) kullanır.

### 1. Oluşturulan Veritabanı Tabloları

1. **`prompt_requests` (`PromptRequest` — `app/models/prompt_request.py`)**
   - Ham kullanıcı isteğini (`raw_prompt`), ek bağlamı (`user_context`), çıktı dilini (`output_language`), hedef AI modunu (`target_mode`: `generic`, `openai`, `anthropic`, `google`), görev profilini (`task_profile`) ve optimizasyon tercihlerini (`prompt_detail_level`: `low|medium|high`, `response_detail_preference`: `minimal|balanced|comprehensive`, `reasoning_preference`: `auto|low|medium|high`) saklar.
   - `raw_prompt` boş bırakılamaz (`CHECK length(trim(raw_prompt)) > 0`) ve geçerli enum değerleri veritabanı düzeyinde `CHECK` kısıtlarıyla doğrulanır.

2. **`workflow_runs` (`WorkflowRun` — `app/models/workflow_run.py`)**
   - Bir `PromptRequest` için başlatılan uçtan uca optimizasyon çalıştırmasını ve durum makinesini (`PENDING`, `GENERATING`, `EVALUATING`, `REVISING`, `COMPLETED`, `FAILED`) temsil eder.
   - Değerlendirme tur sınırlarını veritabanı düzeyinde korur: `max_evaluation_rounds BETWEEN 1 AND 3`, `current_round >= 0 AND current_round <= max_evaluation_rounds`, `completed_rounds >= 0 AND completed_rounds <= current_round`.
   - Yapılandırma anlık görüntüsünü (`config_snapshot`), seçilen nihai sürümü (`final_version_id`), nihai puanı (`final_score`: `0..100`), yeterlilik durumunu (`qualified`), durma nedenini (`stop_reason`), hata alanlarını (`error_code`, `error_message`) ve zaman damgalarını (`created_at`, `started_at`, `finished_at`) saklar.

3. **`prompt_versions` (`PromptVersion` — `app/models/prompt_version.py`)**
   - Yapılandırılmış `CanonicalPrompt` JSON yükünü (`canonical_prompt`), hedefe göre derlenmiş nihai metni (`rendered_prompt`), derleyici üst verisini (`target_mode`, `renderer_name`, `renderer_version`), analiz ve değişiklik özetlerini (`analysis`, `change_summary`) ve sürüm soy ağacını (`parent_version_id`) saklar.
   - Aynı istek altında mükerrer sürüm numarasını `UNIQUE(prompt_request_id, version_number)` ile, aynı ajan çalıştırmasının birden fazla sürüme bağlanmasını `UNIQUE(agent_run_id)` ile engeller.
   - `evaluation_round` dolu olduğunda `evaluation_round BETWEEN 1 AND 3` kısıtını zorunlu tutar.

4. **`agent_runs` (`AgentRun` — `app/models/agent_run.py`)**
   - Çoklu ajan çalıştırmalarının (`agent_name`, `agent_version`, `instruction_version`, `operation`, `evaluation_round`) sağlayıcı/model bilgisini (`provider`, `model`, `parameters`), durumunu (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`), yeniden deneme/onarım sayaçlarını (`attempt_count`, `repair_attempted`), token kullanımını (`input_tokens`, `output_tokens`), gecikmeyi (`latency_ms`), hata mesajını (`error_message`) ve zamanlamasını (`started_at`, `finished_at`) kaydeder.
   - Ajan tamamlanmadan önce bilinmeyen telemetri alanları (`input_tokens`, `output_tokens`, `latency_ms`, `error_message`, `finished_at`) `NULL` olabilir; mevcut olduklarında negatif olamazlar.

### 2. Tablo İlişkileri ve Silme Davranışı (Referential Integrity)

| Kaynak Model / Tablo | İlişki | Hedef Model / Tablo | Yabancı Anahtar ve `ondelete` Kuralı |
|---|---|---|---|
| `PromptRequest` | `1 -> N` (`workflow_runs`) | `WorkflowRun` | `workflow_runs.prompt_request_id` (`NOT NULL`, `RESTRICT`) |
| `PromptRequest` | `1 -> N` (`prompt_versions`) | `PromptVersion` | `prompt_versions.prompt_request_id` (`NOT NULL`, `RESTRICT`) |
| `WorkflowRun` | `1 -> N` (`prompt_versions`) | `PromptVersion` | `prompt_versions.workflow_run_id` (`NULL`, `RESTRICT`) |
| `WorkflowRun` | `1 -> N` (`agent_runs`) | `AgentRun` | `agent_runs.workflow_run_id` (`NOT NULL`, `RESTRICT`) |
| `PromptVersion` | Öz-referans (`parent_version` / `child_versions`) | `PromptVersion` | `prompt_versions.parent_version_id` (`NULL`, `RESTRICT`) |
| `WorkflowRun` | `N -> 0..1` (`final_version`) | `PromptVersion` | `workflow_runs.final_version_id` (`NULL`, `SET NULL`, `use_alter=True`, `post_update=True`) |
| `PromptVersion` | `0..1 <-> 0..1` (`agent_run` / `prompt_version`) | `AgentRun` | `prompt_versions.agent_run_id` (`NULL`, `UNIQUE`, `RESTRICT`) |

- **Tarihsel kayıt koruması:** Hiçbir ilişkide `CASCADE` silme kullanılmaz. Ebeveyn kayıtların yanlışlıkla silinmesi tarihsel yürütme ve sürüm kayıtlarını yok etmez (`RESTRICT` ile engellenir).
- **Belirsiz FK önleme:** `WorkflowRun` ile `PromptVersion` arasında iki ayrı yabancı anahtar yolu (`prompt_versions.workflow_run_id` ve `workflow_runs.final_version_id`) bulunduğundan, her ORM `relationship()` tanımında `foreign_keys=[...]` açıkça belirtilmiştir.

---

## Alembic Göç (Migration) Mimarisi

- **Konfigürasyon:** `backend/alembic.ini` içinde `script_location = %(here)s/migrations` ve `prepend_sys_path = %(here)s` tanımlıdır; ayrıca programatik çağrılar için `app/db/migrations.py` içindeki `build_alembic_config()` yardımcısı mevcuttur. Böylece göçler terminalin o anki çalışma dizininden (CWD) bağımsız çalışır.
- **Asenkron çalışma ve mevcut `DATABASE_URL` entegrasyonu:** `migrations/env.py`, açık bir `sqlalchemy.url` geçersiz kılması yoksa doğrudan `app.core.config.Settings().database_url` değerini okur ve `app.db.session.create_db_engine()` üzerinden asenkron motor oluşturarak göçleri `connection.run_sync(...)` ile yürütür.
- **Uygulama başlangıç politikası:** `Base.metadata.create_all()` uygulama göç stratejisi olarak **kullanılmaz** ve FastAPI açılışında (`lifespan`) otomatik göç **çalıştırılmaz**.
- **SQLite batch desteği ve döngüsel FK çözümü (`0001_initial_schema`):**
  - `workflow_runs.final_version_id -> prompt_versions.id` ile `prompt_versions.workflow_run_id -> workflow_runs.id` arasındaki döngüsel bağımlılık nedeniyle `upgrade()` sırasında önce `prompt_requests`, `workflow_runs` (`final_version_id` sütunu FK olmadan), `agent_runs` ve `prompt_versions` oluşturulur; ardından `op.batch_alter_table("workflow_runs")` ile `fk_workflow_runs_final_version_id_prompt_versions` yabancı anahtarı eklenir.
  - `downgrade()` sırasında `PRAGMA foreign_keys=ON` küresel olarak kapatılmaz; önce `UPDATE workflow_runs SET final_version_id = NULL` ile döngüsel satır referansları temizlenir, PostgreSQL gibi katalog tabanlı lehçelerde döngüsel FK kısıtı düşürülür ve ardından tablolar bağımlılık sırasına göre (`prompt_versions` -> `agent_runs` -> `workflow_runs` -> `prompt_requests`) güvenle kaldırılır.

### Göç Yükseltme (Upgrade) ve Geri Alma (Downgrade)

`backend/` dizininden:

```powershell
cd backend

# Veritabanını en güncel şemaya (head) yükselt
uv run alembic upgrade head

# Mevcut revizyon durumunu görüntüle
uv run alembic current

# Son göç adımını geri al
uv run alembic downgrade -1

# Tüm şemayı başlangıç öncesi boş duruma (base) geri al
uv run alembic downgrade base
```

Depo kök dizininden (CWD bağımsız çalıştırma):

```powershell
uv run --directory backend alembic upgrade head
```

Farklı bir veritabanı hedeflemek için `DATABASE_URL` ortam değişkeni kullanılabilir (böylece geliştiricinin yerel `backend/data/promptforge.db` dosyasına dokunulmaz):

```powershell
$env:DATABASE_URL="sqlite+aiosqlite:///C:/Temp/deneme.db"
uv run --directory backend alembic upgrade head
```

### Göç Testleri Nasıl Çalışır?

`backend/tests/test_migrations.py` içindeki testler:

1. Her test için `pytest` `tmp_path` altında izole bir geçici SQLite dosyası oluşturur ve `monkeypatch.chdir(...)` ile rastgele bir çalışma dizinine geçerek CWD bağımsızlığını doğrular.
2. Geliştiricinin gerçek `backend/data/promptforge.db` veritabanına **kesinlikle dokunmaz** ve dosya oluşturulmadığını doğrular.
3. `command.upgrade(cfg, "head")` sonrasında dört tablonun varlığını, birincil/yabancı anahtarları ve `alembic.autogenerate.compare_metadata` ile ORM modelleri ve göç şeması arasında sıfır sapma (`schema_diffs == []`) olduğunu denetler.
4. Döngüsel `WorkflowRun.final_version` ataması dahil tüm ORM ilişkilerini, `RESTRICT` silme korumasını, `UNIQUE` kısıtlarını ve `max_evaluation_rounds <= 3` / `evaluation_round BETWEEN 1 AND 3` `CHECK` kısıtlarını doğrular.
5. Dolu ve döngüsel referanslı tablolar üzerinde `PRAGMA foreign_keys=ON` açıkken `downgrade("base")` ve yeniden `upgrade("head")` adımlarını test eder.

---

## Prompt Yönetimi API, Şema, Depo ve Servis Katmanı (Milestone 3C-1)

> **Veritabanı Göç Ön Koşulu:** Uygulama açılışında otomatik göç veya `Base.metadata.create_all()` çalıştırılmaz. API uç noktalarını yerel ortamda ilk kez kullanmadan önce şemanın açıkça oluşturulması gerekir:
>
> ```powershell
> cd backend
> uv run alembic upgrade head
> ```

### 1. REST API Uç Noktaları (`app/api/v1/routes/prompt_requests.py`)

| Metot ve Yol | Başarı Durumu | Açıklama |
|---|---|---|
| `POST /api/v1/prompt-requests` | `201 Created` | Yeni bir `PromptRequest` kaydeder, oluşturulan kaynağı döndürür ve `Location: /api/v1/prompt-requests/{id}` başlığı ekler. |
| `GET /api/v1/prompt-requests/{id}` | `200 OK` / `404 Not Found` | UUID ile tek bir prompt isteğini getirir. Kayıt yoksa iç veritabanı detayı sızdırmadan `404` döner; bozuk UUID değerlerinde `422` döner. |
| `GET /api/v1/prompt-requests` | `200 OK` | Kayıtlı prompt isteklerini sayfalı (`page >= 1`, `1 <= page_size <= 100`, varsayılan `page=1`, `page_size=20`) ve deterministik sıralamayla (`created_at DESC, id DESC`) listeler. |

### 2. Pydantic v2 Şemaları ve Doğrulama Kuralları (`app/modules/prompts/schemas.py`)

- **`PromptRequestCreate` (`extra="forbid"`):**
  - Bilinmeyen/fazladan alanları sessizce yok saymak yerine `422` ile reddeder.
  - `raw_prompt`: Zorunlu metin, `1..20.000` karakter; boş veya yalnızca boşluk (`whitespace`) içeren girdiler reddedilir.
  - `user_context`: İsteğe bağlı (`None`), en fazla `20.000` karakter; yalnızca boşluk içeren değerler `None` olarak normalize edilir.
  - `output_language` (`<= 32` karakter) ve `task_profile` (`<= 64` karakter): İsteğe bağlı (`None`); kenar boşlukları kırpılır, boş değerler `None` yapılır.
  - ORM varsayılanlarıyla birebir uyumlu enum alanları (`app/models/enums.py` tek doğruluk kaynağıdır):
    - `target_mode`: `generic` (varsayılan), `openai`, `anthropic`, `google`
    - `prompt_detail_level`: `low`, `medium` (varsayılan), `high`
    - `response_detail_preference`: `minimal`, `balanced` (varsayılan), `comprehensive`
    - `reasoning_preference`: `auto` (varsayılan), `low`, `medium`, `high`
- **`PromptRequestRead` (`from_attributes=True`):** `id` (`UUID`), `raw_prompt`, `user_context`, `output_language`, `target_mode`, `task_profile`, `prompt_detail_level`, `response_detail_preference`, `reasoning_preference` ve `created_at` (UTC `datetime`) alanlarını döner.
- **`PromptRequestListResponse`:** `{ "items": [...], "total": 0, "page": 1, "page_size": 20 }` zarfını döner.

### 3. Depo (`PromptRequestRepository`) ve Servis (`PromptRequestService`) Sorumlulukları

- **`PromptRequestRepository` (`app/modules/prompts/repository.py`):**
  - FastAPI'den tamamen bağımsızdır; dışarıdan verilen `AsyncSession` üzerinde SQLAlchemy 2.0 parametreli sorgular (`select`, `func.count`) çalıştırır.
  - Kayıt ekleme (`create` + `flush`), UUID ile tekil sorgulama (`get_by_id`), toplam kayıt sayımı (`count_all`) ve tüm tabloyu belleğe yüklemeden `OFFSET`/`LIMIT` ile deterministik sayfalı listeleme (`list_paginated`, `ORDER BY created_at DESC, id DESC`) görevlerini üstlenir.
- **`PromptRequestService` (`app/modules/prompts/service.py`):**
  - İş kurallarını ve işlem (transaction) sınırlarını yönetir: yazma işlemlerinde `commit()` uygular, herhangi bir hata oluştuğunda kısmi yazmaları önlemek için açıkça `rollback()` çağırıp hatayı yükseltir.
  - Bulunamayan kayıtlar için HTTP katmanından bağımsız `PromptRequestNotFoundError` alan hatası üretir.

---

## Mevcut Sınırlamalar ve Gelecek Genişleme Noktaları

### Mevcut Sınırlamalar (Milestone 3C-1 Sonu İtibarıyla)

- Yapılandırılmış JSON loglama, `X-Request-ID` ara katmanı ve merkezi RFC 9457 Problem Details hata yanıtları henüz uygulanmamıştır (**Milestone 3C-2** kapsamında eklenecektir).
- Kayıt silme veya geçmiş prompt isteklerini düzenleme uç noktaları bu aşamada kapsam dışıdır.
- Değerlendirme tabloları (`evaluations`, `criterion_scores`, `evaluation_issues`) henüz oluşturulmamıştır (Milestone 7 kapsamında eklenecektir).
- Henüz gerçek veya sahte (`FakeLLMProvider`) LLM yürütmesi, `CanonicalPrompt` derleyicileri veya otomatik revizyon döngüsü uygulanmamıştır.
- Kullanıcı kimlik doğrulama ve proje tabloları ilk MVP kapsamı dışındadır (`ADR-008`).

### Gelecek Genişleme Noktaları

- **Milestone 3C-2 (Planlandı):** Yapılandırılmış JSON loglama, `X-Request-ID` izleme başlığı ve RFC 9457 Problem Details hata yönetimi.
- **Milestone 5–6 (Planlandı):** `AgentRun` kayıtlarını dolduran `LLMGateway`, `PromptEngineerAgent` ve iş akışı başlatma uç noktaları.
- **Milestone 7 (Planlandı):** Yeni bir Alembic revizyonu ile `evaluations`, `criterion_scores` ve `evaluation_issues` tablolarının eklenmesi.
- **Post-MVP PostgreSQL Geçişi:** Lehçeden bağımsız şema ve `env.py` yapısı sayesinde `postgresql+asyncpg://...` bağlantı dizesiyle doğrudan PostgreSQL üzerinde göç çalıştırabilme.

---

## Gereksinimler ve Doğrulama Komutları

- [uv](https://docs.astral.sh/uv/)
- Python 3.12 (`.python-version` ile otomatik seçilir)

```powershell
cd backend
uv sync --python 3.12                                 # .venv oluşturur ve bağımlılıkları kurar
uv run alembic upgrade head                           # yerel veritabanı şemasını oluşturur/günceller
uv run uvicorn app.main:app --reload                  # http://127.0.0.1:8000
uv run pytest -q                                      # tüm backend testleri (M2A + M3A + M3B + M3C-1)
uv run pytest tests/test_database.py -q               # yalnızca M3A veritabanı bağlantı testleri
uv run pytest tests/test_migrations.py -q             # yalnızca M3B ORM ve Alembic göç testleri
uv run pytest tests/test_prompt_repository.py -q      # yalnızca M3C-1 depo ve servis testleri
uv run pytest tests/test_prompt_requests_api.py -q    # yalnızca M3C-1 REST API entegrasyon testleri
uv run ruff check .                                   # lint kontrolü
uv run ruff format --check .                          # format kontrolü
uv run mypy app tests migrations                      # strict tip kontrolü
```
