# PromptForge AI — Frontend

React, Vite, TypeScript (`strict`) ve Tailwind CSS (Vite eklentisi) ile oluşturulmuş kullanıcı arayüzü temeli. Mimari için bkz. [`../docs/architecture.md`](../docs/architecture.md).

## Mevcut durum (Milestone 2A)

- Koyu temalı, duyarlı (responsive) başlangıç uygulama kabuğu
- Gelecekteki prompt optimizasyon çalışma alanı, hedef model seçici (`Universal`, `ChatGPT / GPT`, `Claude`, `Gemini`) ve 3 turlu değerlendirme akışı için salt okunur yer tutucu
- Sahte AI işlevi içermez; AI özellikleri Milestone 5–8.6 arasında eklenecektir
- ESLint, TypeScript `strict` kontrolü ve Vitest birim testleri

## Komutlar

```powershell
cd frontend
npm install          # bağımlılıkları kurar
npm run dev          # http://127.0.0.1:5173
npm run typecheck    # tsc -b (strict kontrol)
npm run lint         # ESLint kontrolü
npm test             # Vitest birim testleri
npm run build        # üretim derlemesi (dist/)
```

