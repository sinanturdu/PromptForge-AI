# PromptForge AI — Frontend

React 19, TypeScript (`strict`), Vite 8, Tailwind CSS v4, React Router ve TanStack Query ile geliştirilmiş kullanıcı arayüzü. Genel sistem mimarisi için bkz. [`../docs/architecture.md`](../docs/architecture.md).

## Mevcut Durum (Milestone 4A — Uygulandı)

- **Özellik tabanlı (feature-based) mimari:** `src/app/`, `src/pages/`, `src/features/prompt-requests/`, `src/components/` ve `src/lib/` katman ayrımı
- **İşlevsel Prompt Gönderim Formu (`/`):** Ham prompt (`raw_prompt`), isteğe bağlı proje bağlamı (`user_context`), hedef yapay zekâ ailesi (`Universal` / `ChatGPT / GPT` / `Claude` / `Gemini`), çıktı dili (`tr` / `en`), isteğe bağlı görev profili (`task_profile`), prompt detay seviyesi (`low` / `medium` / `high`), yanıt detay tercihi (`minimal` / `balanced` / `comprehensive`) ve akıl yürütme tercihi (`auto` / `low` / `medium` / `high`)
- **Gerçek Backend Entegrasyonu:** `POST /api/v1/prompt-requests` ile kalıcı kayıt oluşturma ve `GET /api/v1/prompt-requests/{id}` ile UUID üzerinden detay sorgulama
- **Prompt İsteği Detay Sayfası (`/prompt-requests/:id`):** Doğrudan backend üzerinden veri yükleme (tarayıcı sayfa yenileme / doğrudan bağlantı desteği), yükleniyor ve hata durumları, yeni prompt sayfasına dönüş bağlantısı
- **Merkezi Tipli API İstemcisi (`src/lib/api-client.ts`):** RFC 9457 `application/problem+json` ayrıştırma, alan bazlı `422` doğrulama hatalarını gösterme, sorun giderme için `X-Request-ID` başlığını koruma ve güvenli Türkçe kullanıcı geri bildirimi
- **Otomatik Doğrulama Altyapısı:** ESLint, TypeScript `strict`, Vitest + React Testing Library + MSW test paketi

> **Önemli Not (Mevcut Sınırlamalar):**
> - **AI prompt optimizasyonu henüz aktif değildir.** Kaydedilen hedef model ve akıl yürütme tercihleri gelecekteki derleme/değerlendirme aşamaları için saklanır; herhangi bir LLM sağlayıcısı çalıştırılmaz, otomatik prompt üretilmez ve değerlendirme puanı hesaplanmaz (Milestone 5–8.6 kapsamında eklenecektir).
> - **Prompt geçmişi listeleme sayfası** bu aşamada yer almaz; **Milestone 4B** kapsamında eklenecektir.

---

## Kurulum ve Yerel Çalıştırma

### 1. Backend Servisini Hazırlama ve Başlatma

Frontend uygulaması gerçek API isteklerini yerel FastAPI sunucusuna iletir. Backend açılışında otomatik veritabanı göçü çalıştırılmaz; yerel geliştirme veritabanını ilk kez kullanmadan önce yapılandırılan veritabanı hedefini (`DATABASE_URL` veya varsayılan `backend/data/promptforge.db`) kontrol ederek göçleri manuel olarak uygulayın:

```powershell
cd backend
uv sync --python 3.12

# Yalnızca hedef veritabanı yapılandırmasını kontrol ettikten sonra çalıştırın:
uv run alembic upgrade head

# FastAPI geliştirme sunucusunu başlatın (http://127.0.0.1:8000)
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 2. Frontend Kurulumu ve Başlatma

Ayrı bir terminalde `frontend/` dizininden:

```powershell
cd frontend

# Bağımlılıkları kurun
npm install

# Geliştirme sunucusunu başlatın (http://127.0.0.1:5173)
npm run dev
```

Tarayıcıda `http://127.0.0.1:5173` adresini açarak uygulamayı kullanabilirsiniz.

---

## Geliştirme API Proxy Yapılandırması ve Üretim Sınırlamaları

- **Yerel Geliştirme Proxy'si (`vite.config.ts`):** `npm run dev` çalışırken tarayıcıdan `/api/v1/*` adresine yapılan istekler Vite geliştirme sunucusu tarafından otomatik olarak `http://127.0.0.1:8000` adresindeki FastAPI sunucusuna yönlendirilir. Böylece yerel geliştirmede gereksiz CORS karmaşası oluşmaz.
- **Üretim (Production) Dağıtım Sınırlamaları:**
  1. Vite `server.proxy` ayarı **yalnızca yerel geliştirme sunucusunda (`npm run dev`) çalışır**; `npm run build` ile üretilen statik `dist/` dosyalarında yer almaz.
  2. Üretim ortamında ya aynı origin üzerinde `/api/*` isteklerini FastAPI servisine ileten bir ters vekil sunucu (ör. Nginx, Caddy, bulut yük dengeleyici) kullanılmalı ya da ayrı origin kullanılıyorsa backend tarafında uygun CORS politikası tanımlanıp derleme sırasında `VITE_API_BASE_URL` ayarlanmalıdır.
  3. `/prompt-requests/:id` gibi istemci taraflı rotalarda tarayıcı yenilemesinin (`F5`) çalışması için statik dosya sunucusunun bilinmeyen uygulama rotalarında `index.html` (SPA history fallback) döndürmesi gerekir.

---

## Ortam Değişkenleri (`frontend/.env`)

İsteğe bağlı yerel özelleştirme için `frontend/.env.example` dosyasını `frontend/.env` olarak kopyalayabilirsiniz:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Yalnızca Vite geliştirme sunucusunun `/api` isteklerini yönlendireceği hedef adres. |
| `VITE_API_BASE_URL` | *(boş)* | Boş bırakıldığında istekler göreli `/api/v1/*` adresine gider (Vite proxy veya aynı-origin ters vekil ile uyumlu). Ayrı origin dağıtımlarında ör. `https://api.ornek.com` olarak tanımlanabilir. |

> **Güvenlik Uyarısı:** `VITE_` önekli tüm ortam değişkenleri tarayıcıya gönderilen JavaScript paketine gömülür. API anahtarlarını, parolaları veya gizli kimlik bilgilerini **asla** `VITE_*` değişkenlerine yazmayın.

---

## Prompt Gönderim İş Akışı

1. Kullanıcı `/` sayfasındaki formda ham prompt metnini (zorunlu, boşluk-dışı, en fazla `20.000` karakter), isteğe bağlı proje bağlamını (en fazla `20.000` karakter) ve hedef model / detay / akıl yürütme tercihlerini belirler.
2. İstemci doğrulamasından geçen form `POST /api/v1/prompt-requests` uç noktasına JSON gönderir. Gönderim süresince buton kilitlenerek mükerrer kayıt oluşması önlenir ve idempotent olmayan `POST` istekleri otomatik yeniden denenmez.
3. Sunucu `201 Created` döndüğünde uygulama `/prompt-requests/{id}` detay sayfasına yönlenir ve kaydı `GET /api/v1/prompt-requests/{id}` üzerinden yükler.
4. Hata durumunda (`422`, `413`, `500` veya ağ hatası) kullanıcının yazdığı metin korunur, RFC 9457 Problem Details yanıtı ayrıştırılarak anlaşılır Türkçe geri bildirim ve `X-Request-ID` izleme kodu gösterilir.

---

## Doğrulama ve Otomatik Test Komutları

```powershell
cd frontend
npm run lint         # ESLint statik kod analizi
npm run typecheck    # TypeScript strict tip kontrolü (tsc -b)
npm test             # Vitest + React Testing Library + MSW testleri (veritabanına dokunmaz)
npm run build        # Üretim derlemesi doğrulaması (dist/)
```
