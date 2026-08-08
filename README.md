# Qo'shiq yaratish boti — o'rnatish va joylashtirish qo'llanmasi

## Fayllar tarkibi
- `tegmatentak.py` — botning to'liq kodi
- `requirements.txt` — kerakli Python kutubxonalari
- `.env.example` — sozlamalar shabloni (nusxa oling, `.env` deb nomlang, to'ldiring)
- `Procfile` — Railway/Heroku uchun ishga tushirish buyrug'i

## 1. Birinchi marta o'rnatish

1. `.env.example` faylidan nusxa oling, nomini `.env` deb o'zgartiring
2. `.env` ichidagi qiymatlarni to'ldiring:
   - `BOT_TOKEN` — @BotFather'dan olingan token
   - `GEMINI_API_KEY` — https://aistudio.google.com/apikey dan (bepul) — chekni tekshirish,
     qo'shiq matni yozish VA AI Yordamchi uchun ishlatiladi
   - `DB_PATH` — baza fayli yo'li (pastga qarang — Railway'da alohida e'tibor bering)
3. `pip install -r requirements.txt`
4. `python tegmatentak.py`

## 2. Statistika (foydalanuvchilar, balans) yo'qolmasligi uchun — MUHIM

Bot barcha ma'lumotni **bitta fayl**da saqlaydi: `music_bot.db` (SQLite). Bu fayl
qayerda turishi `DB_PATH` orqali belgilanadi.

### Railway'da ishlatsangiz
Railway har safar qayta deploy qilganda konteynerni **butunlay yangidan** yaratadi —
demak `DB_PATH` oddiy nom bo'lsa (masalan `music_bot.db`), fayl har safar o'chib,
qaytadan bo'sh yaratiladi.

**Yechim — Volume (doimiy disk) ulang:**
1. Railway loyihangizda bot xizmatini (service) oching
2. **Settings → Volumes → + New Volume**
3. Mount path: `/data`
4. **Variables** bo'limiga qo'shing: `DB_PATH=/data/music_bot.db`

Shundan keyin kodni necha marta yangilasangiz ham, baza fayli Volume ichida
saqlanib qoladi va statistika yo'qolmaydi.

### Yangi GitHub akkaunt ochsangiz / botni ko'chirsangiz

1. Eski joydan `music_bot.db` faylini yuklab oling (Railway CLI/Shell orqali)
2. Yangi GitHub akkauntga kodni yuklang — **`.db` faylni GitHub'ga qo'shmang**
3. Yangi Railway loyihasida xuddi shunday Volume ulang
4. Bot birinchi marta ishga tushib, bo'sh baza yaratadi
5. Avval yuklab olgan faylni **aynan shu yo'lga** joylashtiring (ustidan yozing)
6. Botni qayta ishga tushiring — statistika eskicha qoladi

### Oddiy VPS serverda ishlatsangiz
Muammo yo'q — kodni yangilaganda faqat `.py` faylni almashtiring, `.db`ga tegmang.

## 3. Bloklagan foydalanuvchilar statistikadan avtomatik kamayadi

Bot xabar yuborishga uringanda, agar foydalanuvchi botni bloklagan bo'lsa, tizim
buni avtomatik aniqlab, bazada belgilaydi. "📈 Statistika" va "📋 Baza" bo'limlarida
"Faol a'zolar" va "Bloklaganlar" alohida ko'rsatiladi.

## 4. Bot imkoniyatlari qisqacha

- 🎵 Qo'shiq yaratish: mavzu → ovoz turi → matn (o'zi yoki AI yozadi, AI yozgan
  matn tasdiqlanadi — ma'qul/qayta yozdirish/o'zim yozaman) → janr → buyurtma
- ⭐ Tariflar: Plus (40,000/7 kun, 5 qo'shiq) va Pro (65,000/7 kun, 7 qo'shiq),
  har biri orasida 1 soat kutish, tugagach 2 kun bonus qo'shiq
- 💳 Pul kiritish: chek AI (Gemini) orqali avtomatik tekshiriladi, takroriy/soxta
  cheklar aniqlanadi
- 🤖 AI Yordamchi: bot haqidagi savollarga javob beradi
- 🎁 Promokod: admin panelda yaratiladi, foydalanuvchi oddiy xabar sifatida
  yuborsa balans avtomatik to'ladi (bir martalik, maxfiy)
- 🔐 Admin panel: pul berish, xabar yuborish, statistika, namuna qo'shish/o'chirish,
  baza yuklab olish, promokod yaratish

## 5. Kerakli API kalitlari
| Kalit | Nima uchun | Qayerdan olinadi |
|---|---|---|
| `BOT_TOKEN` | Telegram bot | @BotFather |
| `GEMINI_API_KEY` | Chekni tekshirish, qo'shiq matni, AI Yordamchi | aistudio.google.com/apikey |
