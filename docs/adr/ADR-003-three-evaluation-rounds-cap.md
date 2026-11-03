# ADR-003: En Fazla Üç Değerlendirme Turu Sınırı (`evaluation_round` 1–3)

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 3 – Milestone 8)

## Bağlam

"İterasyon" terimi ilk üretimin dahil olup olmadığı konusunda belirsizlik yaratabilmektedir. Ayrıca değerlendirme döngüsünün kesin bir üst sınıra sahip olması gerekmektedir.

## Karar

Kod tabanında ve veritabanında `iteration` yerine yalnızca `evaluation_round` terimi kullanılır:

1. **Tur 1:** İlk üretim (`create`) + 1. değerlendirme
2. **Tur 2:** 1. revizyon (`revise`) + 2. değerlendirme
3. **Tur 3:** 2. revizyon (`revise`) + 3. (son) değerlendirme

Üçüncü değerlendirmeden sonra iş akışı koşulsuz durur. Bu sınır yapılandırma doğrulaması, kod sabiti (`MAX_EVALUATION_ROUNDS_HARD_LIMIT = 3`) ve veritabanı `CHECK` / `UNIQUE` kısıtlarıyla üç katmanda korunur.

## Sonuçlar

- Bir çalıştırmada en fazla 3 prompt sürümü ve 3 değerlendirme oluşur; maliyet ve süre öngörülebilir kalır.

