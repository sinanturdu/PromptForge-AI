# ADR-007: En İyi Değerlendirilmiş Sürümü Temel Alan Revizyon Stratejisi

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 8)

## Bağlam

Bir revizyon turu her zaman bir önceki sürümden daha yüksek puan üretmeyebilir. Kötüleşmiş bir sürümün bir sonraki revizyona girdi olması kalite kaybını büyütebilir.

## Karar

Hem 2. ve 3. turdaki revizyon girdisi hem de çalıştırma sonunda kullanıcıya sunulan nihai sürüm, o ana kadar değerlendirilmiş sürümler arasından öncelik sırasıyla (kalite kapısını geçenler → kritik sorunu olmayanlar → en yüksek `total_score` ve `intent_fidelity`) seçilir.

## Sonuçlar

- Ara turlardaki olası kalite gerilemeleri nihai çıktıyı bozmaz.

