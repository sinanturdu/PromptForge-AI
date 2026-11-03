# ADR-014: Gelecek Sesli Girdi (Speech-to-Text) Mimarisi

- **Durum:** Onaylandı (Planlanan uygulama: Post-MVP / Milestone 10+)

## Bağlam

Kullanıcıların ham fikirlerini klavye yerine sesli olarak, özellikle Türkçe ve İngilizce teknik terimleri karıştırarak hızlıca ifade edebilmesi üretkenliği artıracaktır; ancak ham transkripsiyon hataları doğrudan ajan döngüsüne girerse niyet sapmasına yol açabilir.

## Karar

Post-MVP aşamasında değiştirilebilir bir `TranscriptionProvider` soyutlamasına sahip `VoiceInput` modülü eklenecektir:

- Tarayıcı mikrofon izinleri ve kayıt hataları açık durumlarla yönetilir.
- Türkçe, İngilizce ve karma teknik terminoloji desteklenir (ilk aşamada özel bir transkripsiyon API'si ile).
- Üretilen metin düzenlenebilir bir alanda gösterilir; **kullanıcı metni onaylamadan** prompt optimizasyon çalıştırması başlatılmaz.
- Gizlilik ilkesi gereği ham ses verisi varsayılan olarak kalıcı depolamaya yazılmaz, yalnızca transkripsiyon işlemi süresince işlenir.

## Sonuçlar

- Sesli girdi akışı hem niyet doğruluğunu hem kullanıcı gizliliğini koruyacak şekilde sağlayıcıdan bağımsız kalır.

