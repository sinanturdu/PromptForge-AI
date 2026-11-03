# ADR-001: Modüler Monolit Mimarisi

- **Durum:** Onaylandı (Temel yapı M2A'da kuruldu; modüller M3–M9 boyunca eklenecek)

## Bağlam

PromptForge AI başlangıçta tek geliştirici tarafından artımlı olarak geliştirilmekte, ancak uzun vadede 13 uzman ajanı barındırabilecek bir yapı hedeflemektedir. Dağıtık mikroservisler bu aşamada gereksiz ağ, sözleşme ve dağıtım maliyeti yaratacaktır.

## Karar

Sistem tek bir `backend/` (FastAPI) ve tek bir `frontend/` (React + Vite) uygulamasından oluşan **modüler monolit** olarak inşa edilir. Katmanlar (`api`, `modules`, `workflows`, `agents`, `scoring`, `llm`, `db`) tek yönlü bağımlılık kurallarıyla ayrılır; ajanlar veritabanına veya birbirlerine doğrudan erişemez.

## Sonuçlar

- Yerel geliştirme, test ve hata ayıklama tek süreç üzerinde basitleşir.
- Modül sınırları korunduğu için ileride uzun süren ajan görevlerini ayrı bir işçi sürece (worker) ayırmak yeniden yazım gerektirmez.
