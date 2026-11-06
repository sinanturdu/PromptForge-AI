# PromptForge AI — Frontend

React 19, TypeScript (`strict`), Vite 8, Tailwind CSS v4, React Router ve TanStack Query ile geliştirilmiş kullanıcı arayüzü. Genel sistem mimarisi için bkz. [`../docs/architecture.md`](../docs/architecture.md).

**M5F-4A:** [Typed workflow client and secure same-origin setup](../docs/frontend-workflow-integration.md)
are available as a shared foundation. Existing pages do not start optimization yet.
For workflow API access, build with `npm run build` and serve dist using the verified
backend command's explicit `--frontend-dist` option. Ordinary Vite development blocks
workflow proxy paths; cross-origin execution is unsupported. Both backend flags remain
default-off. The guide covers private-runtime startup, cancellation, Query definitions,
idempotency, uncertainty and offline verification.

## Mevcut Durum (Milestone 4B-1 ve 4B-2 — Uygulandı; 4C-1 — UI/UX İyileştirmeleri)

- **Özellik tabanlı (feature-based) mimari:** `src/app/`, `src/pages/`, `src/features/prompt-requests/`, `src/components/` ve `src/lib/` katman ayrımı
- **Duyarlı (Responsive) Üst Gezinme Çubuğu (`AppHeader`):** Masaüstü, tablet ve mobil ekranlarda gizlenmeden erişilebilen, klavye uyumlu ve aktif rotayı vurgulayan `Yeni Prompt` (`/`) ve `Prompt Geçmişi` (`/history`) bağlantıları
- **İşlevsel Prompt Gönderim Formu (`/`):** Ham prompt (`raw_prompt`), isteğe bağlı proje bağlamı (`user_context`), hedef yapay zekâ ailesi (`Universal` / `ChatGPT / GPT` / `Claude` / `Gemini`), çıktı dili (`tr` / `en`), isteğe bağlı görev profili (`task_profile`), prompt detay seviyesi (`low` / `medium` / `high`), yanıt detay tercihi (`minimal` / `balanced` / `comprehensive`) ve akıl yürütme tercihi (`auto` / `low` / `medium` / `high`)
- **Prompt Geçmişi ve Sunucu Taraflı Sayfalama (`/history`):** `GET /api/v1/prompt-requests?page=...&page_size=10` uç noktasından en yeniden en eskiye sıralı kayıtları getiren, toplam kayıt sayısını gösteren, CSS `line-clamp-3` ile salt sunum düzeyinde güvenli metin önizlemesi sunan ve URL arama parametresi (`?page=2`) ile tarayıcı yenilemesi / Geri-İleri gezinmesini koruyan salt okunur geçmiş arayüzü
- **Gerçek Backend Entegrasyonu ve Önbellek Eşgüdümü:** `POST /api/v1/prompt-requests` ile kalıcı kayıt oluşturma (başarılı kayıtta geçmiş listesi TanStack Query önbelleğini otomatik geçersiz kılma), `GET /api/v1/prompt-requests` ile sayfalı listeleme ve `GET /api/v1/prompt-requests/{id}` ile UUID üzerinden detay sorgulama
- **Prompt İsteği Detay Sayfası (`/prompt-requests/:id`):** Doğrudan backend üzerinden veri yükleme (tarayıcı sayfa yenileme / doğrudan bağlantı desteği), gelinen geçmiş sayfasına (`← Prompt Geçmişine Dön`) veya yeni prompt sayfasına dönüş bağlantıları
- **Merkezi Tipli API İstemcisi (`src/lib/api-client.ts`):** RFC 9457 `application/problem+json` ayrıştırma, alan bazlı `422` doğrulama hatalarını gösterme, sorun giderme için `X-Request-ID` başlığını koruma ve güvenli Türkçe kullanıcı geri bildirimi
- **Otomatik Doğrulama Altyapısı:** ESLint, TypeScript `strict`, Vitest + React Testing Library + MSW test paketi (38 otomatik test)

> **Önemli Not (Mevcut Sınırlamalar):**
> - **AI prompt optimizasyonu henüz aktif değildir.** Kaydedilen hedef model ve akıl yürütme tercihleri gelecekteki derleme/değerlendirme aşamaları için saklanır; herhangi bir LLM sağlayıcısı çalıştırılmaz, otomatik prompt üretilmez ve değerlendirme puanı hesaplanmaz (Milestone 5–8.6 kapsamında eklenecektir).
> - **Salt okunur geçmiş:** Bu aşamada geçmiş kayıtları üzerinde silme, düzenleme, çoğaltma veya arşivleme işlemleri bulunmaz.
> - **Milestone 4B-2 tamamlandı:** Sunucu taraflı anahtar kelime araması (`q`, yaklaşık 350 ms debounce), hedef AI ailesi filtresi (`target_mode`) ve sayfalama durumu URL ile eşgüdümlüdür; detaydan dönüşte korunur.

**Milestone 4C-1:** Yeni prompt sayfası kayıt odaklı sadeleştirildi; mimari önizlemeler sayfa bileşiminden çıkarıldı (kaynak dosyaları korunur). Gezinme, form, klavye odağı ve mobil yerleşim iyileştirildi. API sözleşmeleri ve mevcut varsayılanlar değişmedi.

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

- **Yerel Geliştirme Proxy'si (`vite.config.ts`):** `npm run dev` mevcut PromptRequest ve health isteklerini yerel FastAPI sunucusuna iletir. Workflow yolları proxy öncesinde 403 ile engellenir; workflow erişimi derlenmiş SPA'nın doğrulanmış backend origin'inden sunulmasını gerektirir.
- **Üretim (Production) Dağıtım Sınırlamaları:**
  1. Vite `server.proxy` ayarı **yalnızca yerel geliştirme sunucusunda (`npm run dev`) çalışır**; `npm run build` ile üretilen statik `dist/` dosyalarında yer almaz.
  2. Workflow erişimi için derlenmiş SPA, doğrulanmış yerel backend üzerinden `--frontend-dist` ile aynı origin'de sunulur. Genel/açık dağıtım, ters vekil veya izin verici CORS bu güvenlik modeli tarafından desteklenmez. Eski `VITE_API_BASE_URL` seçeneği workflow istemcisine uygulanmaz.
  3. Doğrulanmış sunucu `/history` ve `/prompt-requests/:id` gibi açıkça izin verilen tarayıcı rotalarında `index.html` döndürür. API, OpenAPI, docs ve bilinmeyen dosya yolları genel SPA fallback tarafından yakalanmaz.

---

## Ortam Değişkenleri (`frontend/.env`)

İsteğe bağlı yerel özelleştirme için `frontend/.env.example` dosyasını `frontend/.env` olarak kopyalayabilirsiniz:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Yalnızca Vite geliştirme sunucusunun `/api` isteklerini yönlendireceği hedef adres. |
| `VITE_API_BASE_URL` | *(boş)* | Yalnızca mevcut PromptRequest istemcisi için eski yapılandırma; workflow istemcisi daima göreli aynı-origin `/api/v1` kullanır. Güvenli yerel SPA derlemesinde boş bırakın. |

> **Güvenlik Uyarısı:** `VITE_` önekli tüm ortam değişkenleri tarayıcıya gönderilen JavaScript paketine gömülür. API anahtarlarını, parolaları veya gizli kimlik bilgilerini **asla** `VITE_*` değişkenlerine yazmayın.

---

## Uygulama Rotaları, Sayfalama ve Durum Yönetimi

### 1. Prompt Gönderim İş Akışı (`/`)

1. Kullanıcı `/` sayfasındaki formda ham prompt metnini (zorunlu, boşluk-dışı, en fazla `20.000` karakter), isteğe bağlı proje bağlamını (en fazla `20.000` karakter) ve hedef model / detay / akıl yürütme tercihlerini belirler.
2. İstemci doğrulamasından geçen form `POST /api/v1/prompt-requests` uç noktasına JSON gönderir. Gönderim süresince buton kilitlenerek mükerrer kayıt oluşması önlenir ve idempotent olmayan `POST` istekleri otomatik yeniden denenmez.
3. Sunucu `201 Created` döndüğünde geçmiş listesi önbelleği (`['prompt-requests', 'list']`) geçersiz kılınır; uygulama `/prompt-requests/{id}` detay sayfasına yönlenir ve kaydı `GET /api/v1/prompt-requests/{id}` üzerinden yükler.
4. Hata durumunda (`422`, `413`, `500` veya ağ hatası) kullanıcının yazdığı metin korunur, RFC 9457 Problem Details yanıtı ayrıştırılarak anlaşılır Türkçe geri bildirim ve `X-Request-ID` izleme kodu gösterilir.

### 2. Prompt Geçmişi, Arama, Filtreleme ve URL Durumu (`/history`)

- **Arama ve filtreleme:** `q` anahtar kelimesi ve `target_mode` filtresi sunucuda uygulanır. Değişiklikler sayfayı 1’e döndürür; URL, yenileme ve Geri/İleri gezinmesiyle eşgüdümlüdür. Detaydan dönüş, sayfa ile birlikte etkin filtreleri korur.

- **Sunucu taraflı sayfalama:** Varsayılan olarak `page = 1` ve `page_size = 10` parametreleriyle `GET /api/v1/prompt-requests?page=1&page_size=10` çağrılır; tüm veri kümesi tarayıcı belleğine yüklenmez. Toplam sayfa sayısı `Math.ceil(total / page_size)` ile hesaplanır (`total = 0` durumunda yanıltıcı `"Sayfa 1 / 0"` gösterilmez).
- **URL tabanlı sayfa durumu (`/history?page=N`):** Seçili sayfa numarası React Router arama parametrelerinde tutulur; tarayıcı yenilendiğinde (`F5`) ve Geri / İleri geçmiş gezinmesinde korunur. Negatif, sıfır veya sayısal olmayan `page` değerleri backend'e geçersiz API isteği göndermeden güvenli biçimde 1. sayfaya (`replace: true`) normalize edilir; veri sayısını aşan sayfa numaraları son geçerli sayfaya yönlendirilir.
- **Yükleniyor, boş ve hata durumları:**
  - **Yükleniyor:** Sayfa geçişlerinde eski sayfanın verileri yeni sayfaya aitmiş gibi gösterilmez; düzen kaymasını önleyen erişilebilir (`role="status"`) iskelet görünümü sunulur.
  - **Boş geçmiş (`total = 0`):** `"Henüz kayıtlı prompt isteğiniz bulunmuyor."` mesajı ve `/` adresine yönlendiren `"İlk Promptunu Oluştur"` bağlantısı gösterilir.
  - **Hata durumu:** `ErrorAlert` bileşeniyle iç sunucu veya SQL ayrıntıları sızdırılmadan Türkçe hata açıklaması, varsa `X-Request-ID` izleme kimliği ve `"Tekrar Dene"` aksiyonu sunulur.
- **Detay gezinmesi:** Her kart `ROUTES.promptRequestDetail(id)` ile mevcut `/prompt-requests/:id` sayfasına gider ve detay sayfasındaki `"← Prompt Geçmişine Dön"` bağlantısı kullanıcının geldiği sayfa numarasını korur.

---

## Doğrulama ve Otomatik Test Komutları

```powershell
cd frontend
npm run lint         # ESLint statik kod analizi
npm run typecheck    # TypeScript strict tip kontrolü (tsc -b)
npm test             # Vitest + React Testing Library + MSW testleri (veritabanına dokunmaz)
npm run build        # Üretim derlemesi doğrulaması (dist/)
```


## Chromium E2E — Milestone 4C-2A ve 4C-2B

E2E testleri gerçek React arayüzünü, Vite proxy’sini ve FastAPI/SQLite backend’ini kullanır; MSW kullanılmaz. Vitest yalnızca `src/**/*.test.{ts,tsx}` dosyalarını, Playwright ise `e2e/**/*.spec.ts` dosyalarını keşfeder.

İlk kurulum (proje kökünden, Windows PowerShell veya Linux):

```sh
cd backend
uv sync --frozen --python 3.12
cd ../frontend
npm ci
npx playwright install chromium
```

Linux CI için Chromium sistem bağımlılıkları da gerekir: `npx playwright install --with-deps chromium`. Backend yorumlayıcısı Windows’ta `backend/.venv/Scripts/python.exe`, Linux’ta `backend/.venv/bin/python` konumundan doğrudan çalıştırılır; test komutu bağımlılık kurmaz.

`frontend/` dizininden:

```sh
npm run test:e2e                     # Tüm 16 Chromium testi
npm run test:e2e -- --repeat-each=3   # Kararlılık kontrolü
npm run test:e2e:ui                  # Etkileşimli Playwright test arayüzü
npm run test:e2e:report              # Son HTML raporunu aç
npm run test:e2e -- e2e/smoke.spec.ts
npm run test:e2e -- e2e/prompt-workflow.spec.ts
npm run test:e2e -- e2e/history-filters.spec.ts
npm run test:e2e -- --grep "reload and a fresh browser context"
npx playwright show-trace test-results/<test-klasörü>/trace.zip
```

### İzolasyon ve yaşam döngüsü

- Her test için işletim sisteminin geçici dizininde benzersiz `promptforge-e2e-*` klasörü ve yeni `e2e.sqlite3` veritabanı oluşturulur. Backend başlatıcısı bu dizinin dışındaki veya mevcut bir veritabanını reddeder.
- Mevcut Alembic göçleri yalnızca açıkça belirtilen bu veritabanına uygulanır. Uygulamanın `DATABASE_URL` değeri de aynı mutlak yola zorlanır. `backend/data/promptforge.db` ve `.env` dosyaları değiştirilmez.
- Backend yalnızca `127.0.0.1:18080`, Vite yalnızca `127.0.0.1:15173` üzerinde çalışır. Dolu portlar hata üretir; mevcut geliştirme sunucuları yeniden kullanılmaz. Backend portu göçlerden önce bağlanır; sağlık yanıtında çalıştırmaya özel servis kimliği doğrulanır.
- E2E Vite yapılandırması yerel `.env` dosyalarını okumaz; miras alınmış `VITE_API_BASE_URL` dahil API adresi ayarları test proxy’sine zorlanır.
- `e2e/fixtures.ts` içindeki otomatik test fixture’ı mevcut `global-setup.ts` başlatma/temizleme fonksiyonunu her test için çağırır. M4C-2A sunucu başlatıcısı ve güvenlik kontrolleri korunur; Playwright yapılandırmasında ayrıca global setup çalıştırılmaz. Normal bitiş, test hatası veya setup hatasında Vite kapatılır, doğrudan başlatılmış Python işlemi sonlandırılır ve çıkışı beklendikten sonra yalnızca oluşturulan geçici klasör silinir. İşlemin zorla öldürülmesi veya sistem çökmesi teardown’u engelleyebilir; böyle bir durumda geçici klasör kalabilir, geliştirme veritabanına yönlendirme yapılmaz.
- UI modunda da her test başında yeni sunucular ve boş veritabanı hazırlanır; test bitiminde temizlenir. Böylece veri yazan testler, tekrarlar ve tek dosya çalıştırmaları birbirini etkilemez; mevcut boş geçmiş smoke testi herhangi bir sırada çalışabilir. Veritabanı reset/silme API’si eklenmez. Test verileri benzersiz kimliklerle oluşturulur. Arama/filtre veri kümeleri gerçek API üzerinden, prompt kaydetme akışları tarayıcı formu üzerinden hazırlanır.

### Kapsam ve hata çıktıları

Toplam **16 test** bulunur:

- `smoke.spec.ts` — Önceki dört test: başlangıç, gezinme, mobil temel kontroller ve boş geçmiş. Assertions korunmuştur; yalnızca ortak fixture import edilir.
- `prompt-workflow.spec.ts` — Altı test: tüm tercihlerle tarayıcıdan kayıt, reload ve yeni browser context üzerinden kalıcılık, boş ve yalnızca boşluk içeren girdilerin POST/veritabanı kaydı oluşturmadan reddedilmesi, geçmişten doğru detaya gidip dönme ve 375 × 812 mobil kayıt/arama/filtre/detay akışı.
- `history-filters.spec.ts` — Altı test: prompt ve proje bağlamında anahtar kelime araması, eşleşmeyen sonuçlar, ChatGPT/GPT–Claude–Gemini filtreleri (Universal dahil kontrol verileriyle), birleşik arama/hedef filtresi, URL parametre sırasından bağımsız doğrudan bağlantı, reload, Geri/İleri, tekil/tüm filtre temizleme ve filtreli sayfalama/detaydan dönüş.

Testler gerçek FastAPI ve Alembic ile hazırlanmış SQLite veritabanını kullanır; MSW, sahte başarılı API yanıtları veya sabit beklemeler kullanılmaz. E2E kapsamı için ek paket veya ortam önkoşulu eklenmemiştir. Tam suite’i üç bağımsız kez çalıştırmak için `npm run test:e2e` komutunu üç kez çalıştırın; `--repeat-each=3` de her testi üç kez yeni veritabanında çalıştırır.

HTML raporundaki başarısız testin Attachments bölümünden trace ve ekran görüntüsüne ulaşılabilir. CLI trace yolu için `<test-klasörü>` yerine gerçek `test-results/` alt klasörünü kullanın. Trace yalnızca başarısız testlerde saklanır; başarılı çalıştırmalarda trace dosyası beklenmez.

Yerel ve CI çalıştırmaları tek worker kullanır; tekrar deneme yoktur. Hata durumunda trace ve ekran görüntüsü `test-results/` altında tutulur. HTML raporu `playwright-report/` altında oluşur; raporlar ve geçici çıktılar Git tarafından yok sayılır. CI ayrı E2E işinde Chromium’u kurar, testleri çalıştırır ve raporları yedi gün saklar.

Playwright yaşam döngüsü ve browser kurulumu: [test fixtures](https://playwright.dev/docs/test-fixtures), [browser kurulumu](https://playwright.dev/docs/browsers).
