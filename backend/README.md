# PromptForge AI — Backend

FastAPI tabanlı modüler monolit backend. Mimari için bkz. [`../docs/architecture.md`](../docs/architecture.md).

## Mevcut durum (Milestone 2A)

- `GET /api/v1/health` endpoint'i
- OpenAPI dokümantasyonu: `/docs` (Swagger UI), `/redoc`, `/api/v1/openapi.json`
- pytest, Ruff ve mypy yapılandırması

Veritabanı, ajanlar ve LLM entegrasyonu **henüz yok**; sonraki milestone'larda eklenecek.

## Gereksinimler

- [uv](https://docs.astral.sh/uv/)
- Python 3.12 (uv, `.python-version` dosyası sayesinde 3.12'yi otomatik seçer)

## Komutlar

```powershell
cd backend
uv sync                                   # .venv oluşturur, bağımlılıkları kurar
uv run uvicorn app.main:app --reload      # http://127.0.0.1:8000
uv run pytest                             # testler
uv run ruff check .                       # lint
uv run ruff format --check .              # format kontrolü
uv run mypy                               # tip kontrolü
```
