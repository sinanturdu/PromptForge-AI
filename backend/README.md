# PromptForge AI — Backend

FastAPI tabanlı modüler monolit backend. Mimari ayrıntılar için bkz. [`../docs/architecture.md`](../docs/architecture.md) ve [`../docs/adr/ADR-009-sqlite-to-postgresql-portability.md`](../docs/adr/ADR-009-sqlite-to-postgresql-portability.md).

## Mevcut durum (Milestone 2A & Milestone 3A)

- `GET /api/v1/health` uç noktası ve otomatik OpenAPI dokümantasyonu (`/docs`, `/redoc`, `/api/v1/openapi.json`)
- `pydantic-settings` tabanlı ortam yapılandırması (`app/core/config.py`)
- SQLAlchemy 2.0 Async motoru, asenkron oturum yönetimi (`DatabaseSessionManager`, `get_db_session`) ve FastAPI `lifespan` entegrasyonu (`app/db/session.py`)
- pytest, Ruff ve `mypy --strict` doğrulama altyapısı

> **Not:** Uygulama veritabanı tabloları, Alembic göçleri (Milestone 3B), depo/servis katmanları (Milestone 3C), AI ajanları ve LLM entegrasyonu **henüz uygulanmamıştır**.

## Veritabanı Altyapısı (Milestone 3A)

- **Gerekli bağımlılıklar:** `sqlalchemy[asyncio]>=2.0,<3.0` (`greenlet` dahil), `aiosqlite>=0.20`, `pydantic-settings>=2.6`.
- **Varsayılan SQLite konumu:** Terminal çalışma dizininden (CWD) bağımsız olarak `backend/data/promptforge.db` (`sqlite+aiosqlite:///<backend_dir>/data/promptforge.db`). `backend/data/` dizini ve tüm SQLite dosyaları Git dışındadır; veritabanı dosyası modül içe aktarımında veya uygulama açılışında değil, ilk gerçek bağlantıda tembel (lazy) olarak oluşturulur.
- **Ortam değişkenleri (`backend/.env`):** Şablon için `backend/.env.example` dosyasına bakınız:
  - `DATABASE_URL`: Varsayılan yerel SQLite yolunu geçersiz kılar (ör. testlerde `sqlite+aiosqlite:///:memory:`).
  - `DATABASE_CONNECT_TIMEOUT`: Bağlantı ve kilit bekleme zaman aşımı (saniye, varsayılan `30.0`).
  - `DATABASE_ECHO`: SQLAlchemy SQL günlükleme bayrağı (varsayılan `false`).
- **Asenkron oturum yönetimi:** `DatabaseSessionManager.session()` ve FastAPI `Depends(get_db_session)` her istek için `AsyncSession` üretir (`autoflush=False`, `expire_on_commit=False`), istisna durumunda otomatik `rollback()` uygular ve oturumu her koşulda kapatır. Uygulama kapanışında FastAPI `lifespan` üzerinden `engine.dispose()` çağrılır.
- **SQLite ve PostgreSQL taşınabilirliği:** Yalnızca SQLite lehçesi algılandığında `PRAGMA foreign_keys=ON` ve `busy_timeout` etkinleştirilir; dosya tabanlı SQLite veritabanlarında ek olarak `PRAGMA journal_mode=WAL` uygulanır (bellek içi `:memory:` veritabanlarına WAL uygulanmaz). PostgreSQL gibi diğer asenkron lehçelerde (`postgresql+asyncpg://...`) SQLite kancaları devreye girmez ve oturum katmanı lehçeden bağımsız çalışır.

## Gereksinimler

- [uv](https://docs.astral.sh/uv/)
- Python 3.12 (`.python-version` ile otomatik seçilir)

## Komutlar

```powershell
cd backend
uv sync --python 3.12                     # .venv oluşturur ve bağımlılıkları kurar
uv run uvicorn app.main:app --reload      # http://127.0.0.1:8000
uv run pytest -q                          # tüm backend testleri
uv run pytest tests/test_database.py -q   # yalnızca veritabanı altyapı testleri
uv run ruff check .                       # lint kontrolü
uv run ruff format --check .              # format kontrolü
uv run mypy app tests                     # strict tip kontrolü
```
