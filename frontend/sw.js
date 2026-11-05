/*
  Chittak service worker — internet bo'lmaganda ham ilova ochilsin.

  Strategiya: avval tarmoq, bo'lmasa — saqlangan nusxa. Internet bor paytda har
  doim yangi fayl va yangi ma'lumot olinadi (eskirgan ilova ko'rinib qolmaydi),
  internet uzilganda esa oxirgi ko'rilgani ko'rsatiladi.

  API javoblari foydalanuvchi bo'yicha alohida saqlanadi (bitta telefonda ikki
  akkaunt bo'lsa, bir-birining ma'lumotini ko'rmaydi). Faqat GET va faqat JSON.
*/

const CACHE = "chittak-v7";
// Faqat bosh sahifa oldindan saqlanadi. js/css esa sahifa so'ragan versiyali
// manzil bilan ("?v=...") birinchi ochilishda keshga tushadi — versiyasiz
// nusxalarni alohida yuklash keraksiz trafik edi.
const SHELL = ["/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => {})
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// So'rov kimdan ekani: ilova seansi yoki Telegram foydalanuvchisi ID'si
function identityOf(request) {
  const session = request.headers.get("X-App-Session");
  if (session) return `s${session.slice(0, 16)}`;
  const initData = request.headers.get("X-Init-Data") || "";
  const match = /user=([^&]+)/.exec(initData);
  if (match) {
    try {
      return `t${JSON.parse(decodeURIComponent(match[1])).id}`;
    } catch (_) {
      /* buzilgan initData */
    }
  }
  return `d${request.headers.get("X-Dev-User") || ""}`;
}

async function networkFirst(request, cacheKey, shouldStore) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (shouldStore(response)) cache.put(cacheKey, response.clone()).catch(() => {});
    return response;
  } catch (error) {
    const cached = await cache.match(cacheKey);
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  const url = new URL(request.url);
  // Yangilanish paytida yangi versiya havolasiga eski server ESKI faylni berishi mumkin — u keshga yozilmaydi
  // (server javobdagi X-Asset-Version'da o'z versiyasini aytadi), aks holda eski menyu qotib qolardi
  const served = response.headers.get("x-asset-version");
  if (served && url.searchParams.has("v") && served !== url.searchParams.get("v")) return response;
  if (response.ok || response.type === "opaque") {
    if (url.searchParams.has("v")) {
      // Yangi versiya keldi — shu faylning eski versiyalari keshda to'planib qolmasin
      const old = await cache.keys();
      await Promise.all(
        old
          .filter((item) => {
            const oldUrl = new URL(item.url);
            return oldUrl.origin === url.origin && oldUrl.pathname === url.pathname && oldUrl.search !== url.search;
          })
          .map((item) => cache.delete(item))
      );
    }
    cache.put(request, response.clone()).catch(() => {});
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) {
    const key = `${url.pathname}${url.search}${url.search ? "&" : "?"}__u=${identityOf(request)}`;
    event.respondWith(
      networkFirst(request, key, (response) => response.ok && (response.headers.get("content-type") || "").includes("json"))
    );
    return;
  }

  // O'zgarmas fayllar: versiyali js/css ("?v=...") va versiyasi manzilda
  // yozilgan tashqi kutubxonalar (MathLive, Google shriftlari) — avval keshdan.
  // Ilova qayta ochilganda ular tarmoqdan umuman so'ralmaydi.
  const immutable =
    (url.origin === self.location.origin && url.searchParams.has("v")) ||
    url.hostname === "cdn.jsdelivr.net" ||
    url.hostname === "fonts.gstatic.com";
  if (immutable) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Sahifa fayllari va tashqi kutubxonalar (Telegram skripti, shriftlar)
  const key = request.mode === "navigate" ? "/" : request;
  event.respondWith(networkFirst(request, key, (response) => response.ok || response.type === "opaque"));
});
