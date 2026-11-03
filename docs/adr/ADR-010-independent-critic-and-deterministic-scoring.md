# ADR-010: Bağımsız Prompt Critic ve Deterministik Puanlama Motoru

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 7)

## Bağlam

Bir ajanın kendi ürettiği promptu değerlendirmesi veya toplam kalite puanının doğrudan LLM aritmetiğine bırakılması yanlı ve tutarsız sonuçlar doğurur.

## Karar

`PromptCriticAgent`, `PromptEngineerAgent`'ın iç gerekçelerini ve önceki toplam puanları görmeden **kör değerlendirme** yapar; yalnızca kriter bazlı 0–10 puanları, kanıt alıntılarını ve yapılandırılmış revizyon önerilerini üretir. 100 üzerinden ağırlıklı toplam puan ve kalite kapısı kararları (`total_score >= 85`, sıfır `critical` sorun, `intent_fidelity >= 7`) Python kodundaki `ScoringEngine` tarafından hesaplanır.

## Sonuçlar

- Kalite değerlendirmesi şeffaf, denetlenebilir ve birim testleriyle %100 doğrulanabilir hale gelir.

