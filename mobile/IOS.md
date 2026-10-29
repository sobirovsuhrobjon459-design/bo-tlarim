# Chittak — iOS ilova

Ilova Android'dagi bilan bir xil: `lib/main.dart` chittak.uz saytini WebView ichida ochadi.
iOS qismi `ios/` papkada, bulutda yig'ish sozlamasi — repo ildizidagi `codemagic.yaml`.

- Bundle ID: `uz.bato.chittak` (Android applicationId bilan bir xil)
- Faqat iPhone, faqat tik holat, iOS 15+
- Kamera (titul skaneri) va galereya ruxsati matnlari: `ios/Runner/Info.plist`

## 1. Apple Developer hisobi ($99 / yil)

1. https://developer.apple.com/programs/enroll — Apple ID bilan kiring (ikki bosqichli tasdiqlash yoqilgan bo'lsin).
2. "Individual" (jismoniy shaxs) — tezroq, 1–2 kun. "Organization" — D-U-N-S raqami kerak, 1–2 hafta.
3. To'lovdan keyin tasdiq xati keladi.

## 2. Ilovani ro'yxatdan o'tkazish

1. https://developer.apple.com/account/resources/identifiers → "+" → App IDs → App →
   Description: `Chittak`, Bundle ID (Explicit): `uz.bato.chittak` → Register.
2. https://appstoreconnect.apple.com → My Apps → "+" → New App:
   Platform iOS, Name `Chittak`, Primary language Uzbek (yoki English), Bundle ID `uz.bato.chittak`, SKU `chittak`.

## 3. App Store Connect API kaliti (Codemagic imzolashi uchun)

1. App Store Connect → Users and Access → Integrations → App Store Connect API → Team Keys → "+".
2. Name: `Codemagic`, Access: **App Manager** → Generate.
3. `.p8` faylni yuklab oling (faqat BIR marta beriladi!), **Issuer ID** va **Key ID** ni yozib oling.
   Bu fayl maxfiy — hech kimga bermang, repoga qo'shmang.

## 4. Codemagic

1. https://codemagic.io → GitHub bilan kiring → `bo-tlarim` repozitoriyasini qo'shing (Flutter App, codemagic.yaml).
2. Team settings → Integrations → App Store Connect → Connect:
   nomi aynan **`Chittak App Store Connect`**, Issuer ID, Key ID, `.p8` fayl.
3. Ilova sahifasi → "Start new build" → workflow **ios-testflight** → Start.
4. ~15–25 daqiqada yig'iladi va TestFlight'ga yuklanadi (Apple qayta ishlashi yana 10–30 daqiqa).

## 5. iPhone'da sinash (TestFlight)

App Store Connect → ilova → TestFlight → Internal Testing → o'zingizni qo'shing →
iPhone'ga **TestFlight** ilovasini o'rnatib, taklif orqali Chittak'ni o'rnating.

## 6. App Store'ga chiqarish

- Skrinshotlar: iPhone 6.9" (1320×2868) — kamida 3 ta.
- Maxfiylik siyosati havolasi (chittak.uz'dagi hujjat), qo'llab-quvvatlash havolasi.
- **App Review Information**: tekshiruvchi kira oladigan sinov hisobi (Telegram'siz kirish yo'li) — busiz rad etiladi.
- **Pullik testlar / kredit sotish**: Apple raqamli kontent uchun faqat o'z to'lov tizimini (In-App Purchase)
  talab qiladi (3.1.1-qoida). iOS ilovada Click/Payme orqali to'lov ko'rinsa, rad etiladi.
