# ADR-012: Prompt Detay Seviyesi, Yanıt Detayı ve Akıl Yürütme Tercihleri

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 8.6; doğrudan API akıl yürütme kontrolü Post-MVP)

## Bağlam

Kullanıcıların hem üretilen promptun ayrıntı yoğunluğunu hem beklenen yanıt kapsamını hem de görev türüne uygun akıl yürütme derinliğini kontrol edebilmesi gerekmektedir. Ancak harici bir sohbet arayüzüne kopyalanan metin ile sağlayıcı API parametreleri aynı teknik etkiye sahip değildir.

## Karar

1. Üç bağımsız kontrol boyutu tanımlanır:
   - `prompt_detail_level`: `low` | `medium` | `high` (düşük detay seçilse bile açık kullanıcı gereksinimleri ve doğruluk kısıtları asla kaldırılmaz).
   - `response_detail_preference`: `minimal` | `balanced` | `comprehensive`.
   - `reasoning_preference`: `auto` | `low` | `medium` | `high`.
2. Görev profillerine göre (mimari tasarım, yeni proje, hata ayıklama, küçük değişiklik, yeniden düzenleme, güvenlik incelemesi) doğruluktan ödün vermeyen, kullanıcı tarafından geçersiz kılınabilir varsayılan çıktı politikaları uygulanır.
3. Üretilen prompt içindeki akıl yürütme yönlendirmesi ile gerçek sağlayıcı API akıl yürütme parametreleri kesin biçimde ayrılır: harici bir uygulamaya kopyalanan promptun modelin gerçek çalışma zamanı akıl yürütme ayarını değiştireceği garanti edilmez; gerçek API kontrolü yalnızca PromptForge AI'ın uyumlu bir API'yi doğrudan çağırdığı Post-MVP akışlarında uygulanır. Sabit oranlı token tasarrufu veya performans artışı vaat edilmez.

## Sonuçlar

- Kullanıcıya yanıltıcı vaatlerde bulunmadan esnek, görev duyarlı ve güvenli optimizasyon kontrolleri sunulur.

