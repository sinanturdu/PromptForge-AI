# ADR-007: En İyi Değerlendirilmiş Sürümü Temel Alan Revizyon Stratejisi

- **Durum:** Onaylandı (Planlanan uygulama: Milestone 8)

## Bağlam

Bir revizyon turu her zaman bir önceki sürümden daha yüksek puan üretmeyebilir. Kötüleşmiş bir sürümün bir sonraki revizyona girdi olması kalite kaybını büyütebilir.

## Karar

Hem 2. ve 3. turdaki revizyon girdisi hem de çalıştırma sonunda kullanıcıya sunulan nihai sürüm, o ana kadar değerlendirilmiş sürümler arasından öncelik sırasıyla (kalite kapısını geçenler → kritik sorunu olmayanlar → en yüksek `total_score` ve `intent_fidelity`) seçilir.

## Sonuçlar

- Ara turlardaki olası kalite gerilemeleri nihai çıktıyı bozmaz.

## Amendment — M5D-3: nullable scores and deterministic ties

The original priority ordering is unchanged. The in-memory selector now makes
it precise: qualified first, then no reported supported critical issue, then
higher known weighted total, then higher known intent fidelity, then the earlier
completed evaluation round. Total and intent are compared separately in that order;
within an otherwise equal tier a known value ranks above an unavailable value.
Two unavailable values tie and comparison proceeds to the next priority.

`unassessable` never receives an invented zero or partial total and never qualifies.
An unassessable result with no reported supported critical issue can outrank a
known critical result because severity precedes score. This does not prove semantic
safety. A best-available selection remains distinct from approval or qualification.
The selector returns an existing completed round; no new evaluation is performed.
Selection/progress are implemented in M5D-3; the actual executor and persistence
remain planned. See [policy contracts and limits](../optimization-policies.md).

## Amendment — M5E-3C: durable selection and actual baseline lineage

The internal persistence service reuses this unchanged selector over reconstructed,
validated completed history. PromptVersion/Evaluation insertion, Critic completion,
completed count and selected-best fields commit atomically. A revised version points
to the PromptVersion underlying its validated baseline Evaluation, never implicitly
to the latest inserted version. Later failures/clarification retain earlier history
and selection. See [atomic persistence](../atomic-evaluation-persistence.md). This
adds no optimizer execution hooks, background runner or public workflow API.

