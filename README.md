# PromptForge AI

**PromptForge AI**, sıradan kullanıcı isteklerini yapılandırılmış, bağımsız olarak değerlendirilmiş ve hedef yapay zekâ modeline uygun biçimde derlenmiş yüksek kaliteli promptlara dönüştürmek üzere tasarlanmış modüler çoklu ajan (multi-agent) yazılım platformudur.

---

## Temel Hedefler

- Ham kullanıcı isteklerindeki belirsizlikleri ve eksik gereksinimleri tespit ederek niyeti koruyan yapılandırılmış promptlar (`CanonicalPrompt`) üretmek
- Üretilen promptları ayrı bir **Prompt Critic** ajanıyla bağımsız olarak denetlemek ve kod tabanlı deterministik puanlama (100 üzerinden) uygulamak
- En fazla **3 değerlendirme turu** (`evaluation_round` 1–3) ile sınırlı, kontrollü bir otomatik revizyon döngüsü işletmek
- Deterministik derleyiciler (Prompt Renderers) aracılığıyla **Universal**, **ChatGPT / GPT**, **Claude** ve **Gemini** hedeflerine uygun çıktılar üretmek
- Uzun vadede yazılım projelerini planlayan, geliştiren, test eden ve güvenlik açısından inceleyen uzman yapay zekâ ajanlarını koordine eden bir platforma dönüşmek

---

## Mevcut Uygulama Durumu (Milestone 4B-1 — Tamamlandı)

> **Önemli Not:** Bu depo şu anda **Milestone 2A (Temel Altyapı)**, **Milestone 3A (Asenkron Veritabanı Bağlantı Temeli)**, **Milestone 3B (İlk Dört ORM Modeli & Alembic Göç Sistemi)**, **Milestone 3C (Prompt Yönetimi REST API, Yapılandırılmış Loglama, `X-Request-ID` ve RFC 9457 Problem Details)**, **Milestone 4A (İşlevsel Prompt Gönderim Arayüzü & Backend Entegrasyonu)** ve **Milestone 4B-1 (Prompt Geçmişi, Sunucu Taraflı Sayfalama ve Gezinme)** aşamalarını tamamlamıştır. Geçmiş listesinde gelişmiş arama ve filtreleme (**Milestone 4B-2**), değerlendirme tabloları (`evaluations`, `criterion_scores`, `evaluation_issues` — Milestone 7), LLM sağlayıcı entegrasyonu, AI ajanları (`PromptEngineerAgent`, `PromptCriticAgent`), `CanonicalPrompt` derleyicileri, token analizi ve sesli girdi (Speech-to-Text) özellikleri **henüz uygulanmamıştır**.

**Şu anda çalışan bileşenler:**

- `backend/` ve `frontend/` ayrımına sahip temiz monorepo yapısı
- Python 3.12 ve `uv` ile yapılandırılmış FastAPI uygulaması (`GET /api/v1/health`, `POST/GET /api/v1/prompt-requests` ve otomatik OpenAPI dokümantasyonu `/docs`)
- `pydantic-settings` ortam yapılandırması (`DATABASE_URL`, `LOG_LEVEL`, `MAX_REQUEST_BODY_BYTES`, CWD'den bağımsız `backend/data/promptforge.db` varsayılanı) ve SQLAlchemy 2.0 Async + `aiosqlite` oturum altyapısı
- SQLAlchemy 2.0 `DeclarativeBase` (`app/db/base.py`), ilk dört temel ORM modeli (`PromptRequest`, `WorkflowRun`, `PromptVersion`, `AgentRun`) ve CWD'den bağımsız asenkron Alembic göç sistemi (`0001_initial_schema`)
- Yapılandırılmış UTC JSON loglama, sunucu taraflı `X-Request-ID` izleme, HTTP gövde boyutu sınırı ve merkezi RFC 9457 Problem Details (`application/problem+json`) hata yönetimi
- React 19, Vite 8, TypeScript (`strict`), Tailwind CSS v4, React Router ve TanStack Query ile geliştirilmiş Türkçe prompt gönderim formu (`/`), sunucu taraflı sayfalamalı ve URL durumlu prompt geçmişi sayfası (`/history`), mobil/masaüstü uyumlu üst gezinme çubuğu, UUID tabanlı prompt detay sayfası (`/prompt-requests/:id`), Problem Details uyumlu API istemcisi ve MSW destekli Vitest test paketi (canlı AI/LLM çağrısı içermez)
- Mimari dokümantasyon ([`docs/architecture.md`](./docs/architecture.md)) ve Mimari Karar Kayıtları ([`docs/adr/`](./docs/adr/README.md))
- GitHub Actions CI iş akışı ([`.github/workflows/ci.yml`](./.github/workflows/ci.yml))

---

## Teknoloji Yığını

| Katman | Teknolojiler |
|---|---|
| **Frontend** | React 19, Vite 8, TypeScript (`strict`), Tailwind CSS v4 (`@tailwindcss/vite`), React Router, TanStack Query, ESLint, Vitest, MSW |
| **Backend** | Python 3.12, FastAPI, Pydantic v2, `pydantic-settings`, Uvicorn, `uv`, Ruff, mypy, pytest |
| **Veritabanı** | SQLite + `aiosqlite` + SQLAlchemy 2.0 Async + Alembic (M3A bağlantı temeli ve M3B ilk 4 ORM modeli + Alembic göç sistemi kuruldu, PostgreSQL uyumlu) |
| **AI Katmanı (Planlanan — M5+)** | Sağlayıcıdan bağımsız `LLMProvider` katmanı, `FakeLLMProvider` ve yapılandırılabilir model/ajan ayrımı |

---

## Planlanan Yapay Zekâ Ajanları

**İlk MVP Çekirdeği (M6 – M8.6):**

1. **Prompt Engineer Agent** (`create` ve `revise` operasyonları)
2. **Prompt Critic / Evaluator Agent** (bağımsız değerlendirme)
3. **Deterministik Revizyon İş Akışı (`RevisionWorkflow`)** ve hedefe özgü **Prompt Renderers**

**Uzun Vadeli Platform Vizyonu (Post-MVP / M10+):**

4. Revision Agent (özel revizyon stratejileri)
5. Master Prompt Architect Agent
6. Project Manager Agent
7. System Architect Agent
8. Frontend Development Agent
9. Backend Development Agent
10. Database Architect Agent
11. QA / Testing Agent
12. Security Review Agent
13. Marketing & Market Research Agent
14. Main Orchestrator Agent

---

## Depo Yapısı

```text
PromptForge-AI/
├── .github/
│   ├── workflows/
│   │   └── ci.yml
│   └── PULL_REQUEST_TEMPLATE.md
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/system.py
│   │   ├── core/config.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   ├── migrations.py
│   │   │   └── session.py
│   │   ├── models/
│   │   │   ├── agent_run.py
│   │   │   ├── enums.py
│   │   │   ├── prompt_request.py
│   │   │   ├── prompt_version.py
│   │   │   └── workflow_run.py
│   │   └── main.py
│   ├── migrations/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 0001_initial_schema.py
│   ├── tests/
│   │   ├── test_database.py
│   │   ├── test_health.py
│   │   └── test_migrations.py
│   ├── .env.example
│   ├── .python-version
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── uv.lock
│   └── README.md
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   ├── config/
│   │   ├── test/
│   │   ├── App.tsx
│   │   ├── index.css
│   │   └── main.tsx
│   ├── .env.example
│   ├── eslint.config.js
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── README.md
├── docs/
│   ├── architecture.md
│   └── adr/
├── .editorconfig
├── .gitattributes
├── .gitignore
└── README.md
```

---

## Kurulum ve Çalıştırma

### Ön Koşullar

- **Python 3.12** ve **[uv](https://docs.astral.sh/uv/)**
- **Node.js 22.12+** (geliştirme ortamı: Node.js 24.11.1) ve **npm**

### Backend Kurulumu ve Çalıştırma

```powershell
cd backend

# Sanal ortamı (.venv) oluştur ve bağımlılıkları kur
uv sync --python 3.12

# Yalnızca yapılandırılan veritabanı hedefini kontrol ettikten sonra şemayı hazırla:
uv run alembic upgrade head

# Geliştirme sunucusunu başlat (http://127.0.0.1:8000)
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- Sağlık kontrolü: `http://127.0.0.1:8000/api/v1/health`
- Swagger UI (OpenAPI): `http://127.0.0.1:8000/docs`

**Backend göç (migration) ve doğrulama komutları:**

```powershell
cd backend
uv run alembic upgrade head
uv run alembic downgrade -1
uv run pytest -q
uv run pytest tests/test_database.py -q
uv run pytest tests/test_migrations.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
```

### Frontend Kurulumu ve Çalıştırma

Yerel geliştirmede Vite sunucusu `/api` isteklerini `http://127.0.0.1:8000` adresindeki FastAPI servisine proxy üzerinden iletir (ayrıntılar ve üretim dağıtım notları için bkz. [`frontend/README.md`](./frontend/README.md)):

```powershell
cd frontend

# Bağımlılıkları kur
npm install

# Geliştirme sunucusunu başlat (http://127.0.0.1:5173)
npm run dev
```

**Frontend doğrulama komutları:**

```powershell
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

---

## Geliştirme Yol Haritası

| Aşama | Başlık | Durum |
|---|---|---|
| **Milestone 1** | Mimari analiz ve teknik planlama | Tamamlandı |
| **Milestone 2A** | Monorepo, backend/frontend temeli, test/lint/CI ve mimari dokümantasyon | Tamamlandı |
| **Milestone 3A** | `pydantic-settings` yapılandırması, SQLite + SQLAlchemy 2.0 Async motoru ve oturum yönetimi | Tamamlandı |
| **Milestone 3B** | İlk dört ORM modeli (`prompt_requests`, `workflow_runs`, `prompt_versions`, `agent_runs`) ve Alembic göç altyapısı | Tamamlandı |
| **Milestone 3C** | Prompt yönetimi API uç noktaları, yapılandırılmış JSON loglama, `X-Request-ID` ve RFC 9457 Problem Details hata yönetimi | Tamamlandı |
| **Milestone 4A** | React Router, TanStack Query, Problem Details uyumlu API istemcisi, Türkçe prompt gönderim formu ve detay sayfası | Tamamlandı |
| **Milestone 4B-1** | Kaydedilen prompt istekleri için sunucu taraflı sayfalamalı geçmiş arayüzü (`/history`), URL `?page=` durumu ve gezinme | **Tamamlandı** |
| **Milestone 4B-2** | Prompt geçmişi için gelişmiş arama ve filtreleme kontrolleri | Planlandı |
| **Milestone 5** | Sağlayıcıdan bağımsız LLM katmanı, `FakeLLMProvider` ve ilk sağlayıcı entegrasyonu | Planlandı |
| **Milestone 6** | `PromptEngineerAgent`, `CanonicalPrompt` ara temsili ve `GenericPromptRenderer` | Planlandı |
| **Milestone 7** | Bağımsız `PromptCriticAgent` ve deterministik puanlama motoru | Planlandı |
| **Milestone 8** | En fazla 3 değerlendirme turlu otomatik revizyon iş akışı | Planlandı |
| **Milestone 8.5** | Hedefe özgü derleyiciler (`openai`, `anthropic`, `google`), hedef seçici ve hedef farkındalıklı değerlendirme | Planlandı |
| **Milestone 8.6** | Prompt detay seviyesi, yanıt detayı, akıl yürütme tercihleri ve görev bazlı politikalar | Planlandı |
| **Milestone 9** | Uçtan uca testler, değerlendirme kalibrasyonu ve ilk MVP sürümü (`v0.1.0`) | Planlandı |
| **Milestone 10+** | Sesli girdi (Speech-to-Text), ek LLM sağlayıcıları, token analizi motoru, doğrudan reasoning API kontrolleri, projeler/auth ve ek uzman ajanlar | Planlandı |
