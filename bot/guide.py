"""
/yordam — ilova qo'llanmasi, foydalanuvchining roliga qarab.

Avval "Siz kimsiz?" so'raladi (GUIDE_ROLES dagi tugmalar), so'ng faqat shu
rolga tegishli qismlar yuboriladi — hamma narsa bir yo'la tashlanmaydi.

Har bir qism alohida xabar bo'lib yuboriladi (Telegram'da bitta xabar 4096
belgidan oshmasligi kerak). Xabarlar himoyalangan (protect_content) holda
yuboriladi — ularni boshqaga uzatib, saqlab yoki nusxalab bo'lmaydi.
Matnni yangilash kerak bo'lsa — faqat shu fayl o'zgartiriladi.
"""

GUIDE_INTRO = """📘 <b>QO'LLANMA — Test Platformasi</b>

Bu bot ichida ilova (Mini App) bor — uni pastdagi <b>PLAY</b> tugmasi ochadi.

<b>Siz kimsiz?</b> Tanlang — sizga kerakli qo'llanmani yuboramiz 👇"""


_STUDENT = """👨‍🎓 <b>TEST TOPSHIRUVCHI UCHUN</b>

1️⃣ PLAY → <b>Test markazi</b> → <b>O'quvchi</b>.
2️⃣ <b>🔍 Yangi test qidirish</b> → o'qituvchi bergan <b>test kodini</b> kiriting.
3️⃣ Test kartasida <b>📋 Shartlar</b> chiqadi:
   • kanalga obuna (✅ / ❌);
   • boshlanish va tugash vaqti;
   • pullik bo'lsa — narxni o'qituvchi kartasiga o'tkazib, <b>chek rasmini</b> yuborasiz; o'qituvchi tasdiqlagach test ochiladi va botda xabar keladi.
   Shart bajarilmagan bo'lsa, tugma orqali bajaring va <b>🔄 Tekshirish</b> ni bosing.
4️⃣ <b>▶️ Testni boshlash</b> — barcha savollar bitta sahifada. Javobni bosib belgilaysiz, yozma javoblar uchun matematik klaviatura bor.
5️⃣ <b>Yakunlash</b> — javoblaringiz o'qituvchiga yuboriladi.

💡 Internet uzilsa ham belgilagan javoblaringiz saqlanib turadi. Vaqt tugasa, test o'zi yakunlanadi.
⚠️ Har bir testni <b>bir marta</b> ishlash mumkin.

<b>Testni boshlay olmayapman?</b>
<b>📋 Shartlar</b> ni ko'ring: ❌ belgisi qaysi shart bajarilmaganini ko'rsatadi.

<b>Natijamni qayerdan bilaman?</b>
Test yakunlangach: <b>O'quvchi</b> → <b>Yakunlangan testlar</b> → <b>📊 Natijani ko'rish</b>. U yerda ball, daraja, o'rningiz va har bir savolda nimani belgilaganingiz, to'g'ri javob nima ekani ko'rinadi. O'qituvchingiz sertifikat ham yuborishi mumkin."""


_TEST_CREATE = """🧑‍🏫 <b>TEST YARATISH</b>

1️⃣ PLAY → <b>Test markazi</b> → <b>O'qituvchi</b> → ism-familiyangizni kiriting.
   🎁 Birinchi kirishda hisobingizga <b>8000 kredit</b> sovg'a qilinadi.
2️⃣ <b>➕ Yangi test yaratish</b>:
   • test nomi, fan, davomiyligi;
   • boshlanish/tugash vaqti (ixtiyoriy);
   • <b>ochiq</b> (o'quvchi o'zi ishlaydi) yoki <b>yopiq</b> (qog'ozda ishlanadi, javoblarni siz kiritasiz);
   • <b>bepul</b> yoki <b>pullik</b> (pullik — o'quvchi pulni <b>sizning kartangizga</b> o'tkazib, chek yuboradi; siz tasdiqlagach test ochiladi);
   • savollar soni, variantlar soni (A–D, A–F) va <b>to'g'ri javoblar kaliti</b>;
   • 🧩 <b>Geometriya savollari</b> (ixtiyoriy) — natijalar Algebra/Geometriya bo'yicha chiqadi.
3️⃣ Saqlang — test <b>kodi</b> chiqadi. Kodni o'quvchilarga yuboring.

📢 <b>Majburiy obuna:</b> barcha testlarda @sukhrobmath kanaliga obuna shart. Xohlasangiz, o'z kanalingizni ham qo'shasiz — bot u yerda <b>administrator</b> bo'lishi kerak."""


_TEST_RESULTS = """📊 <b>NATIJALAR VA SERTIFIKAT</b>

• Natijalar <b>Rasch modeli</b> bo'yicha (Milliy sertifikat uslubida) hisoblanadi: har bir o'quvchiga <b>ball</b> va <b>daraja</b> (A+, A, B+, B, C+, C yoki NC).
• 45 savolli matematika testida <b>Algebra</b> va <b>Geometriya</b> foizi alohida chiqadi. Bo'linishni <b>🧩 Bo'limlar</b> tugmasi bilan o'zgartirasiz.
• <b>🏁 Yakunlash va natijalarni olish</b> — PDF hisobot bot orqali sizga keladi.
• <b>🎓 Sertifikat</b> — har bir o'quvchiga ball va darajasi yozilgan sertifikat (PDF).
• Natijalar o'quvchilarga ko'rsatilmaydi — faqat sizga keladi.

💡 Rasch balli qatnashchilarga nisbatan hisoblanadi — qatnashchi qancha ko'p bo'lsa, ball shuncha aniq."""


_CREDITS = """🎫 <b>KREDIT</b>

<b>Bepul</b> testda har bir o'quvchi natijasi uchun <b>1 kredit</b> sarflanadi. <b>Pullik</b> testda kredit sarflanmaydi.

💳 <b>Pullik test qanday ishlaydi:</b>
1️⃣ Bosh sahifada <b>➕ Karta qo'shish</b> — karta raqamingiz va ism-familiyangiz.
2️⃣ Test yaratishda <b>Pullik test</b> va narxini tanlang.
3️⃣ O'quvchi pulni kartangizga o'tkazib, <b>chek rasmini</b> yuboradi — chek botga <b>✅ Pul tushdi / ❌ Rad etish</b> tugmalari bilan keladi.
4️⃣ Kartangizga pul tushganini tekshirib <b>✅</b> bosing — o'quvchiga test ochiladi va unga xabar boradi.
Pul to'g'ridan-to'g'ri sizga tushadi, platforma ushlamaydi. Cheklar ilovada ham: <b>🧾 Cheklar</b>.

• <b>🛒 Kredit sotib olish</b> — qancha ko'p olsangiz, shuncha arzon:
  1–100 ta — 1 200 so'm, 101–150 — 1 000, 151–300 — 650, 301–500 — 500, 501+ — 450 so'm (1 dona).
• <b>🎁 Promo kod</b> — kodingiz bo'lsa, uni <b>Kredit sotib olish</b> oynasidagi maydonga yozing — qo'shimcha kredit olasiz.

<b>Kreditim tugadi?</b> Kredit tugasa, o'quvchilar testni boshlay olmaydi — oldindan to'ldirib qo'ying."""


_MENTOR = """💻 <b>ONLINE MENTOR UCHUN</b>

Online o'quvchilaringizga test o'tkazib, natijalarni bir joyda yig'ishingiz mumkin:

1️⃣ Test yarating (qanday — keyingi xabarda) va kodini <b>kanalingiz</b> yoki guruhingizga joylang.
2️⃣ Testga <b>o'z kanalingizni</b> majburiy obuna qilib qo'shing — testni ishlamoqchi bo'lganlar avval kanalingizga obuna bo'ladi. Bot kanalda <b>administrator</b> bo'lishi kerak.
3️⃣ <b>Boshlanish va tugash vaqtini</b> belgilang — hamma bir vaqtda ishlaydi (mock imtihon kabi).
4️⃣ Vaqt tugagach, natijalar avtomatik yakunlanadi va <b>PDF hisobot</b> sizga keladi.
5️⃣ Eng yaxshilarga <b>sertifikat</b> yuboring yoki reytingni kanalda e'lon qiling."""


_SCHOOL_START = """🏫 <b>XUSUSIY MAKTAB — BOSHLASH</b>

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

🔒 Xodimlarning telefon, sana va pasport ma'lumotlarini faqat rahbariyat ko'radi.
<b>O'qituvchini chiqarish</b> — faqat admin yoki direktor qiladi."""


_SCHOOL_CLASSES = """📚 <b>SINFLAR VA DAVOMAT</b>

<b>Sinf ochish:</b> Maktab → <b>Boshqa sinflar</b> → <b>➕ Sinf yaratish</b> (admin/direktor). So'ng ⚙️ orqali <b>kurator</b> tayinlanadi.

<b>O'quvchilar</b> (admin, direktor yoki kurator qo'shadi):
Sinf → <b>O'quvchilar</b> → <b>➕ Qo'shish</b> — bittalab yoki <b>Excel / ro'yxatni nusxalab</b> bir yo'la.

<b>Davomat olish:</b>
1️⃣ Sinf → <b>Davomat</b> → <b>📋 Davomat olish</b>.
2️⃣ Dars boshlangan va tugagan vaqtni tanlang.
3️⃣ Hamma oldindan «Bor». O'quvchini bosib holatini almashtirasiz: Bor → Yo'q → Sababli → Kechikdi.
4️⃣ <b>💾 Saqlash</b> → ota-onalarga <b>rasm</b>, <b>matn</b> (faqat kelmaganlar) yoki <b>PDF</b> qilib yuboring.

👁 Har bir fan o'qituvchisi faqat <b>o'z fani</b> davomatini ko'radi. Kurator va rahbariyat — hamma fanni."""


_SCHOOL_TESTS = """📝 <b>SINF TEST NATIJALARI</b>

1️⃣ Sinf → <b>Natijalar</b> → <b>➕ Test natijasini kiritish</b>.
2️⃣ Test nomi, fan, sana va <b>maksimal ball</b>, so'ng har bir o'quvchining balli (kelmagan — bo'sh).
3️⃣ <b>💾 Saqlash</b> — foiz, baho va o'rin avtomatik chiqadi: 86%+ → 5, 71%+ → 4, 56%+ → 3, qolgani → 2.
Natijani <b>🖼 rasm</b> yoki <b>📄 PDF</b> qilib guruhga yuborasiz. Direktor uchun: Maktab → <b>📊 Test natijalari</b>."""


_CONTACT = """💬 <b>Savol bo'lsa:</b> @saidjanovic, @Ulugbeck_dev

<b>Ilova sekin ochilyapti?</b> Internet sekin bo'lsa, tugmada aylanuvchi doiracha chiqadi — kuting, ilova qotmagan.

🔒 <i>Bu qo'llanma himoyalangan — uni uzatib yoki nusxalab bo'lmaydi.</i>"""


# Rol kaliti → (tugma matni, shu rolga yuboriladigan xabarlar).
# Kalit callback_data'da ishlatiladi ("guide:student") — o'zgartirilmasin.
GUIDE_ROLES = {
    "student": ("👨‍🎓 Test topshiruvchi", [_STUDENT, _CONTACT]),
    "teacher": ("🧑‍🏫 Test tuzuvchi", [_TEST_CREATE, _TEST_RESULTS, _CREDITS, _CONTACT]),
    "school": ("🏫 Xususiy maktab asoschisi", [_SCHOOL_START, _SCHOOL_CLASSES, _SCHOOL_TESTS, _CONTACT]),
    "mentor": ("💻 Online mentor", [_MENTOR, _TEST_CREATE, _TEST_RESULTS, _CREDITS, _CONTACT]),
}
