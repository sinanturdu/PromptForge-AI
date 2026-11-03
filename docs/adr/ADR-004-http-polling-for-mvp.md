# ADR-004: İlk MVP İçin HTTP Polling İletişimi

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 4 – Milestone 6)

## Bağlam

Bir optimizasyon çalıştırması 1–3 dakika sürebilir. Kullanıcıya aktif tur bilgisini göstermek gerekir; ancak WebSocket veya SSE bağlantı yönetimi ilk MVP için ek karmaşıklık oluşturur.

## Karar

İlk MVP'de istemci, çalıştırma tamamlanana (`COMPLETED` veya `FAILED`) kadar 2 saniyede bir `GET /api/v1/runs/{id}` uç noktasını sorgular (polling). Terminal duruma ulaşıldığında sorgulama durdurulur. Server-Sent Events (SSE) Post-MVP aşamasına ertelenmiştir.

## Sonuçlar

- Ek gerçek zamanlı iletişim altyapısına ihtiyaç duyulmaz; durum tek doğruluk kaynağı olarak veritabanından okunur.

