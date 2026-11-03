# ADR-008: İlk MVP'de Kimlik Doğrulamasız Yerel Çalışma Modu

- **Durum:** Onaylandı (M2A – M9 yerel çalışma modu; kimlik doğrulama Post-MVP'de eklenecek)

## Bağlam

İlk MVP'nin birincil hedefi çekirdek prompt mühendisliği, bağımsız değerlendirme ve hedef derleme döngüsünü doğrulamaktır. Erken aşamada oturum ve kullanıcı yönetimi eklemek çekirdek değeri geciktirir.

## Karar

MVP yalnızca yerel makine arayüzünde (`127.0.0.1`) kimlik doğrulama olmadan çalışır. Sağlayıcı API anahtarları yalnızca backend ortam değişkenlerinde tutulur ve frontend'e asla iletilmez. Kullanıcı ve proje tabloları Post-MVP'de Alembic göçleriyle (migration) eklenecektir.

## Sonuçlar

- Geliştirme hızı korunur, kullanılmayan şema alanları taşınmaz; yerel ağ dışına açılmadan önce kimlik doğrulama zorunludur.

