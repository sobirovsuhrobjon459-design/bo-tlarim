"""
/yordam — ilova haqida to'liq ma'lumot va ishlatish qo'llanmasi.

Har bir qism alohida xabar bo'lib yuboriladi (Telegram'da bitta xabar 4096
belgidan oshmasligi kerak). Xabarlar himoyalangan (protect_content) holda
yuboriladi — ularni boshqaga uzatib, saqlab yoki nusxalab bo'lmaydi.
Matnni yangilash kerak bo'lsa — faqat shu fayl o'zgartiriladi.
"""

GUIDE_PARTS = [
    """📘 <b>QO'LLANMA — Test Platformasi</b>

Bu bot ichida ilova (Mini App) bor. Uni ochish uchun pastdagi <b>PLAY</b> tugmasini bosing.

Ilovada ikki katta bo'lim bor:

🧪 <b>TEST MARKAZI</b> — testlar
• o'quvchi testni kod bilan topib ishlaydi;
• o'qituvchi test yaratadi va natijalarni oladi.

🏫 <b>TA'LIM MUASSASALARI</b> — maktab boshqaruvi
• xususiy maktab: xodimlar, sinflar, davomat, test natijalari.

Quyidagi xabarlarda har bir bo'lim qadamma-qadam tushuntirilgan.""",

    """👨‍🎓 <b>O'QUVCHI UCHUN</b>

1️⃣ PLAY → <b>Test markazi</b> → <b>O'quvchi</b>.
2️⃣ <b>🔍 Yangi test qidirish</b> → o'qituvchi bergan <b>test kodini</b> kiriting.
3️⃣ Test kartasida <b>📋 Shartlar</b> chiqadi:
   • kanalga obuna (✅ / ❌);
   • boshlanish va tugash vaqti;
   • pullik bo'lsa — to'lov.
   Shart bajarilmagan bo'lsa, tugma orqali bajaring va <b>🔄 Tekshirish</b> ni bosing.
4️⃣ <b>▶️ Testni boshlash</b> — barcha savollar bitta sahifada. Javobni bosib belgilaysiz, yozma javoblar uchun matematik klaviatura bor.
5️⃣ <b>Yakunlash</b> — javoblaringiz o'qituvchiga yuboriladi.

💡 Internet uzilsa ham belgilagan javoblaringiz saqlanib turadi. Vaqt tugasa, test o'zi yakunlanadi.""",

    """🧑‍🏫 <b>O'QITUVCHI UCHUN — TEST YARATISH</b>

1️⃣ PLAY → <b>Test markazi</b> → <b>O'qituvchi</b> → ism-familiyangizni kiriting.
   🎁 Birinchi kirishda hisobingizga <b>8000 kredit</b> sovg'a qilinadi.
2️⃣ <b>➕ Yangi test yaratish</b>:
   • test nomi, fan, davomiyligi;
   • boshlanish/tugash vaqti (ixtiyoriy);
   • <b>bepul</b> yoki <b>pullik</b>;
   • <b>ochiq</b> (o'quvchi o'zi ishlaydi) yoki <b>yopiq</b> (qog'ozda ishlanadi, javoblarni siz kiritasiz);
   • savollar soni, variantlar soni (A–D, A–F) va <b>to'g'ri javoblar kaliti</b>.
3️⃣ Saqlang — test <b>kodi</b> chiqadi. Kodni o'quvchilarga yuboring.

📢 <b>Majburiy obuna:</b> barcha testlarda @sukhrobmath kanaliga obuna shart. Xohlasangiz, o'z kanalingizni ham qo'shasiz — bot u yerda <b>administrator</b> bo'lishi kerak.""",

    """📊 <b>NATIJALAR, KREDIT VA PUL</b>

• <b>Natijalarni ko'rish</b> — har bir o'quvchining balli va javoblari.
• <b>🏁 Yakunlash va natijalarni olish</b> — natijalar Rasch modeli bo'yicha hisoblanib, PDF hisobot bot orqali sizga keladi.
• Natijalar o'quvchilarga ko'rsatilmaydi — faqat sizga keladi.

🎫 <b>Kredit:</b> bepul testda har bir o'quvchi natijasi uchun 1 kredit sarflanadi.
• <b>🛒 Kredit sotib olish</b> — qancha ko'p olsangiz, shuncha arzon:
  1–100 ta — 1 200 so'm, 101–150 — 1 000, 151–300 — 650, 301–500 — 500, 501+ — 450 so'm (1 dona).
• <b>🎁 Promo kod kiritish</b> — kodingiz bo'lsa, qo'shimcha kredit olasiz (har bir kod bir marta).

💰 <b>Pullik testlar:</b> o'quvchi to'lagan pulning sizga tegishli qismi balansingizga tushadi → <b>💸 Pul yechish</b>.""",

    """🏫 <b>XUSUSIY MAKTAB — BOSHLASH</b>

<b>Maktab ochish (admin):</b>
PLAY → Ta'lim muassasalari → Xususiy maktab → <b>Maktabni ro'yxatdan o'tkazish</b> → viloyat, tuman, maktab nomi, telefon va tug'ilgan sanangiz. Maktabni ochgan kishi — <b>👑 Admin</b>.

<b>O'qituvchilarni qo'shish:</b>
Maktab → <b>Xodimlar</b> → pastdagi yumaloq <b>➕</b> tugma → Telegram'da o'qituvchini tanlang. Unga <b>bir martalik havola</b> boradi (24 soat amal qiladi) — u havolani bosib, kodsiz qo'shiladi.
O'qituvchi qo'shilishda telefon raqami va tug'ilgan sanasini kiritadi (pasport — ixtiyoriy).

<b>Lavozimlar</b> (admin beradi — xodimni bosing):
• 🎓 <b>Direktor</b> — sinf ochadi, kurator tayinlaydi, sinfni o'chiradi;
• 🎖 <b>Zam direktor</b> — o'qituvchi qo'shadi, davomatlarni tahrirlaydi;
• ⭐ <b>Kurator</b> — o'z sinfining o'quvchilarini boshqaradi;
• <b>O'qituvchi</b> — davomat oladi, test natijalarini kiritadi.

🔒 Xodimlarning telefon, sana va pasport ma'lumotlarini faqat rahbariyat ko'radi.""",

    """📚 <b>SINFLAR VA DAVOMAT</b>

<b>Sinf ochish:</b> Maktab → <b>Boshqa sinflar</b> → <b>➕ Sinf yaratish</b> (admin/direktor). So'ng ⚙️ orqali <b>kurator</b> tayinlanadi.

<b>O'quvchilar</b> (admin, direktor yoki kurator qo'shadi):
Sinf → <b>O'quvchilar</b> → <b>➕ Qo'shish</b> — bittalab yoki <b>Excel / ro'yxatni nusxalab</b> bir yo'la.

<b>Davomat olish:</b>
1️⃣ Sinf → <b>Davomat</b> → <b>📋 Davomat olish</b>.
2️⃣ Dars boshlangan va tugagan vaqtni tanlang.
3️⃣ Hamma oldindan «Bor». O'quvchini bosib holatini almashtirasiz: Bor → Yo'q → Sababli → Kechikdi.
4️⃣ <b>💾 Saqlash</b> → ota-onalarga <b>rasm</b>, <b>matn</b> (faqat kelmaganlar) yoki <b>PDF</b> qilib yuboring.

👁 Har bir fan o'qituvchisi faqat <b>o'z fani</b> davomatini ko'radi. Kurator va rahbariyat — hamma fanni (fan bo'yicha filtr bilan).""",

    """📝 <b>SINF TEST NATIJALARI</b>

Sinfda o'tkazilgan nazorat natijalarini kiritish:
1️⃣ Sinf → <b>Natijalar</b> → <b>➕ Test natijasini kiritish</b>.
2️⃣ Test nomi, fan, sana va <b>maksimal ball</b>.
3️⃣ Har bir o'quvchining balli (kelmagan bo'lsa — bo'sh qoldiring). Ball maksimaldan oshmaydi.
4️⃣ <b>💾 Saqlash</b> — foiz, baho va o'rin avtomatik chiqadi:
   86%+ → 5, 71%+ → 4, 56%+ → 3, qolgani → 2.

Natijani <b>🖼 rasm</b> yoki <b>📄 PDF</b> qilib olib, guruhga yuborasiz yoki chop etasiz.

👁 Natijalarni testni kiritgan o'qituvchi va rahbariyat ko'radi. Direktor uchun: Maktab → <b>📊 Test natijalari</b> → sinfni tanlang.""",

    """❓ <b>SAVOL-JAVOB</b>

<b>Ilova sekin ochilyapti?</b>
Internet sekin bo'lsa, tugmada aylanuvchi doiracha chiqadi — kuting, ilova qotmagan.

<b>Testni boshlay olmayapman?</b>
Test kartasidagi <b>📋 Shartlar</b> ni ko'ring: ❌ belgisi qaysi shart bajarilmaganini ko'rsatadi.

<b>Maktabdan chiqmoqchiman?</b>
O'qituvchini maktabdan faqat admin yoki direktor chiqaradi — ularga murojaat qiling.

<b>Kreditim tugadi?</b>
O'qituvchi → <b>🛒 Kredit sotib olish</b> yoki promo kod.

💬 <b>Murojaat uchun:</b> @saidjanovic, @Ulugbeck_dev

🔒 <i>Bu qo'llanma himoyalangan — uni uzatib yoki nusxalab bo'lmaydi.</i>""",
]
