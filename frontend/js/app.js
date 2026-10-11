/*
  Mini App'ning butun ekran mantig'i shu faylda.

  Tuzilishi:
  0) Telegram tema (dark/light) sozlash
  1) Yordamchi funksiyalar (api so'rovi, matnni xavfsiz chiqarish, formatlash)
  2) Bosh sahifa
  3) O'quvchi oqimi: qidirish → to'lov → test ishlash → natija → reyting
  4) O'qituvchi oqimi: kirish/ro'yxatdan o'tish → dashboard → yangi test → natijalar/PDF
*/

const appElement = document.getElementById("app");
const telegram = window.Telegram?.WebApp;

/* ---------- 0) Telegram tema (dark/light) ---------- */

function applyTheme() {
  const isDark = telegram
    ? telegram.colorScheme === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;

  document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
}

if (telegram) {
  telegram.ready();
  telegram.expand();
  telegram.onEvent("themeChanged", applyTheme);

  // Sahifani pastga aylantirayotganda Mini App tasodifan yopilib ketmasin
  // (ayniqsa test ishlayotganda). Yopish faqat tepadagi "×" tugmasi orqali.
  try {
    telegram.disableVerticalSwipes?.();
  } catch (_) {
    /* eski Telegram versiyasida bu imkoniyat yo'q */
  }
  // Mini App tasodifan yopilib qolmasin: yopishda Telegram DOIM "Rostdan yopasizmi?" deb so'raydi
  try {
    telegram.enableClosingConfirmation?.();
  } catch (_) {
    /* eski Telegram versiyasida bu imkoniyat yo'q */
  }
}

/*
  Tasodifan chiqib ketishdan himoya.

  Mini App'ni YOPISH — har doim so'raladi (yuqorida yoqilgan). Ilova ichida
  orqaga qaytish esa faqat saqlanmagan forma bo'lsa so'raladi (confirmLeave).
*/
let unsavedChanges = false;

function setExitProtection(enabled) {
  // Yopish tasdig'i doimiy yoqilgan — bu yerda faqat yoqiladi, hech qachon o'chirilmaydi
  if (!enabled) return;
  try {
    telegram?.enableClosingConfirmation?.();
  } catch (_) {
    /* Telegram'dan tashqarida — e'tiborsiz */
  }
}

function markUnsaved() {
  if (unsavedChanges) return;
  unsavedChanges = true;
  setExitProtection(true);
}

async function confirmLeave() {
  if (!unsavedChanges) return true;
  return askConfirm("Saqlanmagan ma'lumotlar yo'qoladi. Baribir chiqasizmi?");
}

applyTheme();

// Telegram'siz (oddiy brauzerda) sinash uchun shartli foydalanuvchi ID
const DEV_USER_ID = "1";

let isAdmin = false;
let isTeacher = false;
let quizState = null;
let quizTimerId = null;

// O'qituvchi dashboard'idagi testlar ro'yxati qaysi bo'limda turibdi: "active" | "closed"
let teacherTestsTab = "active";

/* ---------- 1) Yordamchi funksiyalar ---------- */

/*
  Sahifalar uchun eslangan ma'lumot ("oldin ko'rsat, keyin yangila").
  Bo'limga kirish darhol bo'lishi uchun oxirgi ma'lumot shu yerda turadi;
  har qanday o'zgartiruvchi so'rovdan (POST/PUT/DELETE) keyin tozalanadi —
  eski holat ko'rinib qolmasin.
*/
const pageDataCache = new Map();
// O'zgartirishdan keyingi "eski" nusxa: bo'lim shu bilan DARHOL ochiladi,
// yangisi fonda keladi va almashtiriladi (ekran bo'sh turib qolmasin)
const staleDataCache = new Map();
// Sinf tafsilotlari (id → sinf) — qayta kirganda darhol ochish uchun
const classDetailCache = new Map();

async function api(path, { method = "GET", body, keepalive = false } = {}) {
  if (method !== "GET") {
    pageDataCache.forEach((value, key) => staleDataCache.set(key, value));
    pageDataCache.clear();
    // O'zgartirishdan keyin sinf eskirgan ma'lumot bilan ochilmasin
    classDetailCache.clear();
  }

  // Alohida o'qituvchi tokeni yo'q — Telegram initData o'zi kifoya,
  // "o'qituvchi" ekanligi backend'da shu odamga tegishli Teacher yozuvi
  // borligi orqali aniqlanadi.
  const headers = {
    "X-Init-Data": telegram?.initData || "",
  };
  if (!telegram?.initData) {
    headers["X-Dev-User"] = DEV_USER_ID;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    // keepalive: sahifa yopilayotganda ham so'rov oxirigacha yetib boradi.
    // Oddiy so'rov bu paytda uzilib ketardi — javoblarni oxirgi marta
    // saqlash uchun aynan shu kerak.
    keepalive,
  });

  if (!response.ok) {
    let message = "Xatolik yuz berdi";
    try {
      const errorData = await response.json();
      if (typeof errorData.detail === "string") message = errorData.detail;
    } catch (_) {
      /* javobda JSON bo'lmasligi mumkin — standart xabar qoladi */
    }

    // Holat kodi xatoga biriktiriladi: chaqiruvchi ayrim xatolarni
    // (masalan "obuna bo'lmagansiz") alohida ko'rsatishi uchun kerak.
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  return response.status === 204 ? null : response.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function formatNumber(value) {
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function formatDuration(totalSeconds) {
  // Uzoq testlarda "147:42" tushunarsiz — "2 soat 27 min" deb yozamiz.
  const total = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;

  if (hours) return `${hours} soat ${minutes} min`;
  if (minutes) return `${minutes} min ${seconds} s`;
  return `${seconds} s`;
}

function parseUtc(isoString) {
  // Backend vaqtni har doim UTC'da saqlaydi va mintaqasiz yuboradi
  // ("2026-10-06T10:35:00"). JavaScript bunday matnni MAHALLIY vaqt deb
  // o'qiydi, shuning uchun "Z" qo'shamiz — aks holda vaqt mintaqa farqicha
  // (Toshkentda 5 soat) xato bo'lib, boshlangan test "hali boshlanmagan"
  // bo'lib ko'rinardi.
  if (!isoString) return null;

  const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(isoString);
  return new Date(hasTimezone ? isoString : `${isoString}Z`);
}

function formatDateTime(isoString) {
  const date = parseUtc(isoString);
  if (!date) return "";

  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} - ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function utcIsoToLocalInput(isoString) {
  // localTimeToUtcIso ning teskarisi: <input type="datetime-local"> uchun mahalliy vaqt
  const date = parseUtc(isoString);
  if (!date) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function localTimeToUtcIso(value) {
  // <input type="datetime-local"> mahalliy vaqtni beradi — uni backend
  // kutayotgan UTC ko'rinishiga o'tkazamiz.
  return value ? new Date(value).toISOString().slice(0, 19) : null;
}

/*
  Ro'yxatdagi kartochkalar (xodimlar, sinflar, o'quvchilar) aylantirilganda
  ekranga kirib kelishi bilan pastdan silliq chiqadi. Birinchi ko'ringanlar
  ketma-ket (biroz kechikish bilan) chiqadi.
*/
const REVEAL_SELECTOR = ".member-cards .member-line, .class-list .class-row, .student-list .student-card, .school-tiles .school-tile, .panel.pick-list .pick-item, .result-cards .result-line";
let revealObserver = null;
let revealBatch = 0; // bir vaqtda ko'ringanlar ketma-ket chiqishi uchun

function revealListItems() {
  const items = document.querySelectorAll(REVEAL_SELECTOR);
  if (!items.length) return;
  if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  // Bitta kuzatuvchi — qayta chizilganda ham pastdagi kartochkalar "yo'qolib" qolmaydi
  revealObserver = revealObserver || new IntersectionObserver(
    (entries) => {
      entries
        .filter((entry) => entry.isIntersecting)
        .forEach((entry) => {
          const item = entry.target;
          revealObserver.unobserve(item);
          item.classList.remove("reveal");
          // Web Animations — CSS holatiga bog'liq emas, har doim silliq chiqadi
          item.animate(
            [
              { opacity: 0, transform: "translateY(14px) scale(0.98)" },
              { opacity: 1, transform: "none" },
            ],
            { duration: 380, delay: Math.min(revealBatch, 6) * 45, easing: "cubic-bezier(0.22, 1, 0.36, 1)", fill: "backwards" }
          );
          revealBatch += 1;
        });
      setTimeout(() => {
        revealBatch = 0;
      }, 120);
    },
    { rootMargin: "0px 0px -20px 0px", threshold: 0.05 }
  );
  items.forEach((item) => {
    if (item.dataset.revealed) return;
    item.dataset.revealed = "1";
    item.classList.add("reveal"); // ko'ringuncha yashirin
    revealObserver.observe(item);
  });
}

// Sahifa yoki ro'yxat qayta chizilganda — yangi kartochkalar kuzatiladi
new MutationObserver(() => {
  if (document.querySelector(REVEAL_SELECTOR.split(", ").map((s) => `${s}:not([data-revealed])`).join(", "))) {
    requestAnimationFrame(revealListItems);
  }
}).observe(appElement, { childList: true, subtree: true });

function showPage(html) {
  // Menyu avval yashiriladi — kerak bo'lsa, sahifa o'zi qaytadan ko'rsatadi.
  // Ikkalasi bitta qadamda bo'lgani uchun ekranda miltillash sezilmaydi.
  hideBottomNav();
  document.querySelector(".sheet-backdrop")?.remove();
  unsavedChanges = false;
  setExitProtection(false);
  // Ochiq matematik klaviatura yangi sahifa ustida qolib ketmasin
  if (activeMathField || window.mathVirtualKeyboard?.visible) closeMathKeyboard();
  appElement.innerHTML = `<div class="page">${html}</div>`;
  window.scrollTo(0, 0);
}

const TOAST_VISIBLE_MS = 2400;
const TOAST_FADE_MS = 220; // CSS'dagi .toast.is-leaving davomiyligi bilan bir xil

function showToast(message) {
  // Avvalgi xabar hali turgan bo'lsa olib tashlanadi — aks holda ikkalasi
  // ustma-ust tushib, o'qib bo'lmay qolardi.
  document.querySelector(".toast")?.remove();

  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.appendChild(toast);

  // Avval asta so'nadi, keyin o'chiriladi — birdan yo'qolib qolmaydi
  setTimeout(() => {
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), TOAST_FADE_MS);
  }, TOAST_VISIBLE_MS);
}

/*
  Yozma javoblar — MathLive maydoni (<math-field>) va uning standart
  matematik klaviaturasi (raqamlar, √, kasr, daraja, π, sin/cos/log, harflar).

  Klaviaturani ochish va yopishni MathLive'ning o'ziga emas, shu yerdagi kodga
  topshiramiz (math-virtual-keyboard-policy="manual"). Sabab: Telegram ichida
  bo'sh joyga bosish maydondan fokusni olmaydi, shuning uchun MathLive'ning
  "fokus ketsa yopilsin" mantig'i ishlamay, klaviatura ochiq qolib ketardi.
  Endi klaviatura quyidagi hollarda albatta yopiladi:
    • maydon va klaviaturadan tashqariga bosilganda;
    • "✕ Yopish" tugmasi yoki klaviaturadagi "Enter" bosilganda;
    • boshqa sahifaga o'tilganda (showPage);
    • ilova yashirilganda (Telegram yopilsa yoki fonga o'tsa).

  MathLive CDN'dan yuklanmay qolsa, maydon oddiy matn maydoniga aylanadi va
  o'quvchi "2sqrt3", "pi/5" kabi yozadi — server ikkalasini ham tushunadi
  (qarang: backend _normalize_text_answer).
*/

function isMathLiveReady() {
  return Boolean(window.customElements?.get("math-field") && window.mathVirtualKeyboard);
}

function answerInputHint() {
  return isMathLiveReady()
    ? "Maydonga bosing — matematik klaviatura ochiladi (√, kasr, daraja, π)"
    : "Yozish namunalari: 2sqrt3 · pi/5 · 1/2 · x^2 · 0,5";
}

function latexToPlain(value) {
  /*
    LaTeX javobni oddiy matnga aylantiradi ("2\sqrt{3}" -> "2√(3)",
    "\frac{\pi}{5}" -> "(π)/(5)"). Natijalar ro'yxatida va MathLive
    yuklanmagan holatda oddiy maydonda ko'rsatish uchun.
  */
  let text = String(value ?? "").trim();
  if (!text.includes("\\")) return text; // allaqachon oddiy matn

  text = text
    .replace(/\\left|\\right/g, "")
    .replace(/\\placeholder\{\}/g, "")
    .replace(/\\[,;!]/g, "")
    .replace(/\\[dt]frac/g, "\\frac")
    .replace(/\^\{?\\circ\}?|\\degree/g, "°");

  for (let step = 0; step < 5; step += 1) {
    const next = text.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, "($1)/($2)");
    if (next === text) break;
    text = next;
  }

  return text
    .replace(/\\sqrt/g, "√")
    .replace(/\\pi/g, "π")
    .replace(/\\(cdot|times)/g, "×")
    .replace(/\\exponentialE/g, "e")
    .replace(/\\([a-zA-Z]+)/g, "$1") // qolgan buyruqlar: "\sin" -> "sin"
    .replace(/\{/g, "(")
    .replace(/\}/g, ")");
}

function mathFieldHtml(initialValue, extraAttributes = "") {
  if (isMathLiveReady()) {
    return `<math-field class="math-input" math-virtual-keyboard-policy="manual" ${extraAttributes}>${escapeHtml(
      initialValue || ""
    )}</math-field>`;
  }

  // Zaxira: oddiy matn maydoni. autocapitalize/autocorrect o'chiriladi —
  // telefon "sqrt" ni boshqa so'zga "tuzatib" yubormasligi uchun.
  return `<input class="input math-input" type="text" autocomplete="off" autocorrect="off"
                 autocapitalize="off" spellcheck="false" enterkeyhint="done"
                 value="${escapeHtml(latexToPlain(initialValue))}" ${extraAttributes} />`;
}

function readMathValue(field) {
  return String(field.value || "").trim();
}

/* ---------- Klaviaturani boshqarish ---------- */

let activeMathField = null;
let keyboardPaddingTimer = null;

function isTouchScreen() {
  return window.matchMedia?.("(pointer: coarse)").matches || "ontouchstart" in window;
}

function keyboardCloseButton() {
  let button = document.getElementById("math-keyboard-close");
  if (button) return button;

  button = document.createElement("button");
  button.id = "math-keyboard-close";
  button.className = "keyboard-close";
  button.type = "button";
  button.hidden = true;
  button.textContent = "✕ Yopish";
  // pointerdown'da yopamiz: "click" kutilsa, ba'zi telefonlarda tugma
  // bosilishi bilan fokus o'zgarib, bosish boshqa joyga tushib qolardi.
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeMathKeyboard();
  });
  document.body.appendChild(button);
  return button;
}

function syncKeyboardLayout() {
  const keyboard = window.mathVirtualKeyboard;
  const isVisible = Boolean(keyboard?.visible);

  keyboardCloseButton().hidden = !isVisible;
  clearTimeout(keyboardPaddingTimer);

  if (isVisible) {
    const height = Math.round(keyboard.boundingRect?.height || 0) || 280;
    document.documentElement.style.setProperty("--keyboard-height", `${height}px`);
    document.body.classList.add("keyboard-open");
  } else {
    // Sahifa tagidagi bo'sh joy biroz kechikib olinadi: aks holda sahifa
    // sakrab, o'quvchi bosgan tugma barmog'i ostidan qochib ketardi.
    document.body.classList.remove("keyboard-open");
    keyboardPaddingTimer = setTimeout(() => {
      document.documentElement.style.setProperty("--keyboard-height", "0px");
    }, 400);
  }
}

function scrollFieldIntoView(field) {
  // Klaviatura chiqib bo'lgach maydon uning ustida ko'rinsin
  [80, 350].forEach((delay) =>
    setTimeout(() => {
      if (activeMathField !== field || !field.isConnected) return;
      const keyboardHeight = Math.round(window.mathVirtualKeyboard?.boundingRect?.height || 0);
      const visibleBottom = window.innerHeight - keyboardHeight - 56; // 56 — "Yopish" tugmasi uchun joy
      const rect = field.getBoundingClientRect();
      if (rect.bottom > visibleBottom || rect.top < 0) {
        window.scrollBy({ top: rect.bottom - visibleBottom + 12, behavior: "smooth" });
      }
    }, delay)
  );
}

function openMathKeyboard(field) {
  activeMathField = field;
  if (document.activeElement !== field) field.focus();

  // Kompyuterda (Telegram Desktop) jismoniy klaviatura bor — ekrandagisi
  // o'zi ochilmaydi, kerak bo'lsa maydondagi klaviatura belgisi bilan ochiladi.
  if (!isTouchScreen()) return;

  window.mathVirtualKeyboard.show({ animate: true });
  syncKeyboardLayout();
  scrollFieldIntoView(field);
}

function closeMathKeyboard() {
  const field = activeMathField;
  activeMathField = null;

  window.mathVirtualKeyboard?.hide?.({ animate: true });
  // Fokus ham olinadi — aks holda maydonga qayta bosilganda "focus" hodisasi
  // bo'lmaydi va ba'zi telefonlarda klaviatura qayta ochilmay qolardi.
  if (field?.isConnected) field.blur();
  if (document.activeElement?.tagName === "MATH-FIELD") document.activeElement.blur();

  if (document.getElementById("math-keyboard-close")) syncKeyboardLayout();
}

let mathKeyboardWatchersReady = false;

function setupMathKeyboardWatchers() {
  if (mathKeyboardWatchersReady || !isMathLiveReady()) return;
  mathKeyboardWatchersReady = true;

  // Ovoz fayllari yuklanmasin (Telegram ichida keraksiz va sekinlashtiradi)
  try {
    window.MathfieldElement.soundsDirectory = null;
  } catch (error) {
    /* eski versiyada bu xossa bo'lmasligi mumkin */
  }

  const keyboard = window.mathVirtualKeyboard;
  // Klaviatura qaysi yo'l bilan ochilmasin/yopilmasin (maydondagi klaviatura
  // belgisi, klaviaturaning o'z tugmasi) — "Yopish" tugmasi va pastki bo'sh
  // joy har doim uning haqiqiy holatiga moslanadi.
  ["virtual-keyboard-toggle", "geometrychange"].forEach((eventName) =>
    keyboard.addEventListener(eventName, syncKeyboardLayout)
  );

  // Maydon va klaviaturadan tashqariga bosilsa — yopiladi
  document.addEventListener(
    "pointerdown",
    (event) => {
      if (!keyboard.visible && !activeMathField) return;
      const target = event.target;
      if (target.closest?.("math-field, .ML__keyboard, #math-keyboard-close")) return;
      closeMathKeyboard();
    },
    true
  );

  // Har qanday maydon fokus olganda (kim qo'shgan bo'lmasin) — "manual".
  // MathLive fokusdan 300 ms keyin aynan shu xossani tekshiradi.
  document.addEventListener(
    "focusin",
    (event) => {
      const field = event.target.closest?.("math-field");
      if (field && field.mathVirtualKeyboardPolicy !== "manual") field.mathVirtualKeyboardPolicy = "manual";
    },
    true
  );

  // Telegram fonga o'tsa yoki yopilsa — klaviatura ochiq qolib ketmasin
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) closeMathKeyboard();
  });
}


// Har bir maydonning "o'zgardi" funksiyasi — MathLive kech yuklanib, oddiy
// maydon almashtirilganda yangisiga ham ulash uchun kerak.
const mathFieldHandlers = new WeakMap();

function setupMathField(field, onChange) {
  if (mathFieldHandlers.has(field)) return; // ikki marta ulanib qolmasin
  mathFieldHandlers.set(field, onChange);

  field.addEventListener("input", () => onChange(field, readMathValue(field)));

  if (field.tagName !== "MATH-FIELD") {
    // Oddiy maydon: "Tayyor" (Enter) bosilganda telefon klaviaturasi yopiladi
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        field.blur();
      }
    });
    return;
  }

  // Atributning o'zi yetmaydi: MathLive ba'zan yangi maydonga "auto" ni
  // qo'yib yuboradi va keyin har fokusda klaviaturani o'zi ochardi.
  field.mathVirtualKeyboardPolicy = "manual";

  // MathLive menyusi ("≡" tugmasi) Telegram ichida chalkashtiradi — o'chiriladi
  field.menuItems = [];

  // Klaviatura maydonga BOSILGANDA ochiladi ("focus"da emas): shunda Telegram
  // qayta ochilib, fokus o'z-o'zidan qaytganda klaviatura kutilmaganda chiqmaydi.
  field.addEventListener("pointerup", () => openMathKeyboard(field));

  // Kompyuterda Tab bilan kelinganda ham shu maydon "faol" hisoblansin
  field.addEventListener("focus", () => {
    activeMathField = field;
  });

  // Klaviaturadagi Enter (⏎) — javob tayyor, klaviatura yopiladi
  field.addEventListener("change", () => {
    if (window.mathVirtualKeyboard?.visible) closeMathKeyboard();
  });
}

function setupMathFields(container, onChange) {
  setupMathKeyboardWatchers();
  container.querySelectorAll(".math-input").forEach((field) => setupMathField(field, onChange));
}

/*
  MathLive sahifa ochilgandan keyin yuklanadi ("defer"). Agar shu orada yozma
  maydonli sahifa (masalan, test) ochilib bo'lgan bo'lsa, undagi oddiy
  maydonlar MathLive maydoniga almashtiriladi — kiritilgan qiymat saqlanadi.
*/
// MathLive shriftlar manzilini o'zi noto'g'ri topadi (".../mathlive.min.js/fonts/"
// — 404), shunda formulalar va klaviatura belgilari buzuq chiqardi. Shuning
// uchun aniq ko'rsatamiz; versiya index.html'dagi bilan bir xil bo'lsin.
const MATHLIVE_FONTS_URL = "https://cdn.jsdelivr.net/npm/mathlive@0.111.0/fonts";

window.customElements?.whenDefined?.("math-field").then(() => {
  try {
    window.MathfieldElement.fontsDirectory = MATHLIVE_FONTS_URL;
  } catch (error) {
    /* shriftsiz ham ishlaydi, faqat ko'rinishi oddiyroq bo'ladi */
  }

  // mathVirtualKeyboard element ro'yxatdan o'tgan zahoti tayyor bo'lmasligi mumkin
  setTimeout(() => {
    if (!isMathLiveReady()) return;
    setupMathKeyboardWatchers();

    document.querySelectorAll("input.math-input").forEach((input) => {
      const holder = document.createElement("div");
      holder.innerHTML = mathFieldHtml(input.value);
      const replacement = holder.firstElementChild;

      ["placeholder", "data-ref", "data-text-ref"].forEach((name) => {
        if (input.hasAttribute(name)) replacement.setAttribute(name, input.getAttribute(name));
      });

      const onChange = mathFieldHandlers.get(input);
      input.replaceWith(replacement);
      if (onChange) setupMathField(replacement, onChange);
    });

    document.querySelectorAll(".answer-hint").forEach((hint) => {
      hint.textContent = answerInputHint();
    });
  }, 0);
});

async function copyToClipboard(text, message = null) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // Ba'zi webview'larda clipboard API yopiq — zaxira yo'l
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  telegram?.HapticFeedback?.notificationOccurred?.("success");
  // Uzun matnni (masalan, taklif xabari) butunlay ko'rsatmaslik uchun o'z xabari berilishi mumkin
  showToast(message || `📋 Nusxalandi: ${text}`);
}

// Kod yozilgan joyni bir bosish bilan nusxalash. Tinglovchi "capture"
// bosqichida ishlaydi — shunda test kartasining ichidagi kodni bosganda
// karta ochilib ketmaydi.
appElement.addEventListener(
  "click",
  (event) => {
    const target = event.target.closest("[data-copy]");
    if (!target) return;

    event.stopPropagation();
    event.preventDefault();
    copyToClipboard(target.dataset.copy);
  },
  true
);

function openExternalLink(url) {
  // Telegram ichida havolani brauzerda ochish uchun alohida usul bor; oddiy
  // brauzerda esa window.open ishlatiladi. t.me havolalari Telegram ilovasida
  // ochilishi kerak, qolganlari (masalan Payme to'lov sahifasi) brauzerda.
  if (!url) return;

  const isTelegramLink = /^https?:\/\/t\.me\//.test(url);

  if (isTelegramLink && telegram?.openTelegramLink) {
    telegram.openTelegramLink(url);
  } else if (telegram?.openLink) {
    telegram.openLink(url);
  } else {
    window.open(url, "_blank");
  }
}

function askConfirm(message) {
  // Telegram ichida chiroyli dialog ko'rsatadi, oddiy brauzerda esa odatdagi confirm.
  // showConfirm faqat Telegram 6.2+ da ishlaydi: eski versiyada xato beradi va
  // javob hech qachon qaytmaydi — tugma "jim" qolardi. Shuning uchun versiya
  // tekshiriladi va har qanday xatoda oddiy confirm'ga o'tiladi.
  return new Promise((resolve) => {
    const telegramConfirm = telegram?.showConfirm && telegram.isVersionAtLeast?.("6.2");
    if (telegramConfirm) {
      try {
        telegram.showConfirm(message, (confirmed) => resolve(Boolean(confirmed)));
        return;
      } catch (_) {
        /* pastdagi oddiy confirm */
      }
    }
    resolve(window.confirm(message));
  });
}

const TRASH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6"/>
</svg>`;

const COPY_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="9" y="9" width="12" height="12" rx="2"/>
  <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>
</svg>`;

function deleteButton(attribute, value) {
  return `<button class="delete-button" type="button" ${attribute}="${value}" title="O'chirish">${TRASH_ICON}</button>`;
}

function copyCodeChip(code) {
  return `<span class="copy-code" data-copy="${code}"><b>${code}</b>${COPY_ICON}</span>`;
}

// O'qituvchi bo'limining pastki menyusi: Testlar | Yaratish | Natijalar
const TEACHER_TABS = [
  { id: "tests", label: "Testlar", icon: "☰" },
  { id: "create", label: "Yaratish", icon: "＋" },
  { id: "results", label: "Natijalar", icon: "▥" },
];

function navMarkup() {
  return TEACHER_TABS.map(
    (tab) => `
      <button class="nav-item" type="button" data-nav="${tab.id}">
        <span class="nav-icon">${tab.icon}</span>
        <span>${tab.label}</span>
      </button>`
  ).join("");
}

function showBottomNav(activeTab) {
  // Menyu sahifadan TASHQARIDA turadi: bo'lim almashganda u qayta
  // chizilmaydi, faqat belgilangan tugma o'zgaradi — shunda sakrash bo'lmaydi.
  let host = document.getElementById("bottom-nav-host");

  if (!host) {
    host = document.createElement("div");
    host.id = "bottom-nav-host";
    host.className = "bottom-nav";
    host.innerHTML = navMarkup();
    document.body.appendChild(host);

    host.addEventListener(
      "click",
      guarded(async (event) => {
        const button = event.target.closest("[data-nav]");
        if (!button || button.classList.contains("active")) return;
        if (!(await confirmLeave())) return;

        // Avval tugma darhol belgilanadi — bosilgani shu zahoti bilinadi
        const tab = button.dataset.nav;
        host.querySelectorAll(".nav-item").forEach((item) => {
          item.classList.toggle("active", item === button);
        });

        if (tab === "create") {
          renderNewTestForm("rash");
          return;
        }

        // Ma'lumot yuklanguncha bo'sh karta ko'rinadi
        renderSkeleton(tab);
        if (tab === "tests") await renderTeacherDashboard();
        else await renderResultsTab();
      })
    );
  }

  host.hidden = false;
  host.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.nav === activeTab);
  });
}

function hideBottomNav() {
  // O'qituvchi bo'limi va maktab bo'limi menyulari — ikkalasi ham yashiriladi
  ["bottom-nav-host", "school-nav-host", "class-nav-host"].forEach((id) => {
    const host = document.getElementById(id);
    if (host) host.hidden = true;
  });
}

function pageHeader(title) {
  // Chapdagi "←" — "Test markazi" papkasiga (o'quvchi/o'qituvchi tanlash) qaytaradi
  return `
    <div class="page-header">
      <button class="back-button" type="button" data-action="back" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(title)}</h2>
    </div>`;
}

function wireTeacherChrome(activeTab) {
  document.querySelector('[data-action="back"]')?.addEventListener(
    "click",
    guarded(async () => {
      if (await confirmLeave()) renderTestFolder();
    })
  );
  showBottomNav(activeTab);
}

function testBadges(test) {
  const enrollment =
    test.enrollment_type === "closed"
      ? `<span class="badge badge-purple">Yopiq test</span>`
      : `<span class="badge badge-blue">Ochiq test</span>`;

  const status =
    test.status === "closed"
      ? `<span class="badge badge-gray">Yopilgan</span>`
      : `<span class="badge badge-green">Faol</span>`;

  return `${enrollment}${status}`;
}

// Hozir bajarilayotgan tugmalar — ikkinchi bosish e'tiborsiz qoldiriladi
const busyElements = new WeakSet();

/*
  Yuklanish belgisi: amal 0.15 soniyadan uzoq cho'zilsa — ekran tepasida
  yuguruvchi chiziq va bosilgan tugma/kartada aylanuvchi doiracha. Shunda
  sekin internetda ham ilova "qotib qolgandek" ko'rinmaydi.
*/
let topLoaderCount = 0;

function startTopLoader() {
  topLoaderCount += 1;
  let bar = document.getElementById("top-loader");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "top-loader";
    document.body.appendChild(bar);
  }
  bar.classList.add("active");
}

function stopTopLoader() {
  topLoaderCount = Math.max(topLoaderCount - 1, 0);
  if (!topLoaderCount) document.getElementById("top-loader")?.classList.remove("active");
}

function guarded(handler) {
  /*
    Xatoni chiroyli xabar qilib ko'rsatadi va IKKI MARTA BOSISHDAN himoya
    qiladi: amal tugaguncha o'sha tugma qayta ishlamaydi. Ilgari sekin
    internetda tugma ikki marta bosilsa, so'rov (to'lov, yakunlash, pul
    yechish) ikki marta ketishi mumkin edi.
  */
  return async (...args) => {
    // currentTarget birinchi "await"gacha olinishi shart — keyin u null bo'ladi
    const target = args[0]?.currentTarget || null;
    if (target && busyElements.has(target)) return;
    if (target) busyElements.add(target);
    const isButton = target instanceof HTMLButtonElement;
    if (isButton) target.disabled = true;
    let loading = false;
    const loaderTimer = setTimeout(() => {
      loading = true;
      target?.classList.add("is-loading");
      startTopLoader();
    }, 150);

    try {
      await handler(...args);
    } catch (error) {
      showToast(error.message || "Xatolik yuz berdi");
    } finally {
      clearTimeout(loaderTimer);
      if (loading) {
        target?.classList.remove("is-loading");
        stopTopLoader();
      }
      if (target) busyElements.delete(target);
      if (isButton) target.disabled = false;
    }
  };
}

function animateCountUp(element, targetValue) {
  const startTime = performance.now();
  const duration = 1200;

  function step(now) {
    const progress = Math.min(1, (now - startTime) / duration);
    element.textContent = Math.round(targetValue * progress) + "%";
    if (progress < 1) requestAnimationFrame(step);
  }

  requestAnimationFrame(step);
}

/* ---------- 2) Bosh sahifa ---------- */

function renderSkeleton(activeTab) {
  showPage(`
    <div class="skeleton skeleton-line" style="width:65%;height:26px"></div>
    <div class="skeleton skeleton-line" style="width:45%;margin-bottom:24px"></div>
    <div class="skeleton skeleton-card"></div>
    <div class="skeleton skeleton-card"></div>
  `);

  // Yuklanayotganda ham pastki menyu joyida qolsin
  if (activeTab) showBottomNav(activeTab);
}

// Bosh sahifadagi rol ikonkalari (emoji o'rniga — har qurilmada bir xil chiqadi)
const ROLE_ICONS = {
  student: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d="M22 10 12 5 2 10l10 5 10-5Z"/>
    <path d="M6 12v5c0 1 2.7 2.5 6 2.5s6-1.5 6-2.5v-5"/></svg>`,
  teacher: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="13" rx="2"/>
    <path d="M8 9h8M8 12.5h5M12 17v3"/></svg>`,
  admin: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 4 6v5c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6l-8-3Z"/>
    <path d="m9 12 2 2 4-4"/></svg>`,
  folder: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>
    <path d="M8 13h8"/></svg>`,
  school: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18M5 21V10l7-5 7 5v11"/>
    <path d="M10 21v-5h4v5M9 11h.01M15 11h.01"/></svg>`,
  privateSchool: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18M5 21V11l7-5 7 5v10"/>
    <path d="m12 9.5.9 1.8 2 .3-1.45 1.4.35 2-1.8-.95-1.8.95.35-2-1.45-1.4 2-.3.9-1.8Z"/><path d="M10 21v-3h4v3"/></svg>`,
  course: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15Z"/>
    <path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5M9 8h7M9 11.5h5"/></svg>`,
  club: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/>
    <path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18M3.5 9h17M3.5 15h17"/></svg>`,
};

function roleCard(action, icon, title, description) {
  return `
    <div class="card card-entrance role-card" data-action="${action}">
      <div class="card-icon">${icon}</div>
      <div class="card-body"><h3>${title}</h3><p>${description}</p></div>
    </div>`;
}

/*
  Murojaat uchun adminlar — savol, to'lov yoki muammo bo'lsa shularga yoziladi.
  Almashtirish kerak bo'lsa, faqat shu ro'yxat o'zgartiriladi (botda ham shunday ro'yxat bor).
*/
const SUPPORT_CONTACTS = ["saidjanovic", "Ulugbeck_dev"];

/*
  Admin rasmi (Telegram profil rasmi). Ostida doim bosh harf turadi — rasm
  bo'lmasa yoki yuklanmasa ham bo'sh joy emas, chiroyli harf ko'rinadi.
*/
function supportAvatarHtml(username, size, className) {
  return `
    <span class="support-avatar ${className}">
      <span>${escapeHtml(username.charAt(0).toUpperCase())}</span>
      <img src="https://t.me/i/userpic/${size}/${username}.jpg" alt="" loading="lazy" onerror="this.remove()" />
    </span>`;
}

// Kichkina "Murojaat uchun" tugmasi — bosilsa adminlar profillari chiqadi
function supportCardHtml(note = "Savol, taklif yoki muammo bo'lsa — yozing", { bottom = false } = {}) {
  return `
    <div class="support-row${bottom ? " support-row-bottom" : ""}">
      <button type="button" class="support-pill" data-open-support="${escapeHtml(note)}">
        <span class="support-avatars">
          ${SUPPORT_CONTACTS.map((username) => supportAvatarHtml(username, 160, "support-mini")).join("")}
        </span>
        💬 Murojaat uchun
      </button>
    </div>`;
}

function openSupportSheet(note) {
  openSheet(`
    <h3 style="margin:0 0 4px">💬 Murojaat uchun</h3>
    <p class="subtitle" style="margin:0 0 12px">${escapeHtml(note || "Savol, taklif yoki muammo bo'lsa — yozing")}</p>
    ${SUPPORT_CONTACTS.map(
      (username) => `
        <div class="support-profile">
          ${supportAvatarHtml(username, 320, "support-photo")}
          <div class="support-profile-info">
            <b>@${username}</b>
            <small>Platforma admini</small>
          </div>
          <button type="button" class="btn support-write" data-support-user="${username}">✉️ Yozish</button>
        </div>`
    ).join("")}
    <button class="btn btn-ghost" data-sheet-close style="margin-top:10px">Yopish</button>
  `);
}

// Bitta umumiy tinglovchi — kontakt tugmasi qaysi sahifada bo'lmasin ishlaydi
document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-open-support]");
  if (opener) {
    openSupportSheet(opener.dataset.openSupport);
    return;
  }
  const link = event.target.closest("[data-support-user]");
  if (link) openExternalLink(`https://t.me/${link.dataset.supportUser}`);
});

function renderHome(userName, backendOk) {
  const greeting = userName ? `Xush kelibsiz,<br>${escapeHtml(userName)}` : "Test Platformasiga<br>xush kelibsiz";

  // Hammasi joyida bo'lganda hech narsa ko'rsatilmaydi — faqat haqiqiy xatoda ogohlantirish chiqadi.
  const statusHtml = backendOk ? "" : `<p class="status">Backend bilan aloqa yo'q ❌</p>`;

  showPage(`
    <h1 class="fade-up">${greeting}</h1>
    <p class="subtitle fade-up">Bilimingizni sinang va natijangizni kuzating.</p>

    ${roleCard("school-folder", ROLE_ICONS.school, "TA'LIM MUASSASALARI", "Xususiy maktab, kurslar, to'garaklar")}
    ${roleCard("tests", ROLE_ICONS.folder, "TEST MARKAZI", "Testlar")}
    ${isAdmin ? roleCard("admin", ROLE_ICONS.admin, "ADMIN", "Umumiy statistika va boshqaruv") : ""}

    ${statusHtml}
    ${supportCardHtml(undefined, { bottom: true })}
  `);

  document.querySelector('[data-action="school-folder"]').addEventListener("click", renderSchoolFolder);
  document.querySelector('[data-action="tests"]').addEventListener("click", renderTestFolder);
  document.querySelector('[data-action="admin"]')?.addEventListener(
    "click",
    guarded(async () => {
      await renderAdminDashboard();
    })
  );
}

/*
  Bosh sahifada ikkita papka bor:
    • "Ta'lim muassasalari" — Xususiy maktab, O'quv kurslari, To'garaklar;
    • "Test markazi" — O'quvchi va O'qituvchi bo'limlari.
  Bo'limlar ichidagi "←" tugmalari o'z papkasiga qaytaradi (bosh sahifaga emas).
*/
function renderSchoolFolder() {
  // Bo'limlar ro'yxati — yangisini qo'shish uchun shu yerga bitta qator yetadi
  // Oxirgi ustun — bo'lim ochilganda nima bo'lishi (hozircha tayyorlari kam)
  const sections = [
    ["private-school", ROLE_ICONS.privateSchool, "XUSUSIY MAKTAB", "Maktab oching yoki taklif bilan qo'shiling", enterPrivateSchool],
    ["course", ROLE_ICONS.course, "O'QUV KURSLARI", "O'quv markazlari va kurslar", () => renderComingSoon("O'quv kurslari", ROLE_ICONS.course)],
    ["club", ROLE_ICONS.club, "TO'GARAKLAR", "Sport, san'at va boshqa to'garaklar", () => renderComingSoon("To'garaklar", ROLE_ICONS.club)],
  ];

  showPage(`
    <div class="page-header">
      <button class="back-button" type="button" data-action="home" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">Ta'lim muassasalari</h2>
    </div>
    <p class="subtitle fade-up">Bo'limni tanlang</p>

    ${sections.map(([action, icon, title, description]) => roleCard(action, icon, title, description)).join("")}
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  sections.forEach(([action, , , , open]) => {
    document.querySelector(`[data-action="${action}"]`).addEventListener("click", guarded(open));
  });
}

function renderTestFolder() {
  showPage(`
    <div class="page-header">
      <button class="back-button" type="button" data-action="home" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">Test markazi</h2>
    </div>
    <p class="subtitle fade-up">Kim sifatida kirasiz?</p>

    ${roleCard("student", ROLE_ICONS.student, "O'QUVCHI", "Testlarni topish va ishlash")}
    ${roleCard("teacher", ROLE_ICONS.teacher, "O'QITUVCHI", "Test yaratish va natijalarni boshqarish")}
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  document.querySelector('[data-action="student"]').addEventListener("click", guarded(renderStudentHome));
  document.querySelector('[data-action="teacher"]').addEventListener("click", guarded(enterTeacherArea));
}

/* ---------- Sinflar va davomat ---------- */

// Davomat holatlari: bosilganda shu tartibda almashadi
const ATTENDANCE_STATES = [
  { id: "present", label: "Bor", icon: "✅" },
  { id: "absent", label: "Yo'q", icon: "❌" },
  { id: "excused", label: "Sababli", icon: "🟡" },
  { id: "late", label: "Kechikdi", icon: "⏰" },
];
const ATTENDANCE_STATE_BY_ID = Object.fromEntries(ATTENDANCE_STATES.map((state) => [state.id, state]));

function nextAttendanceState(current) {
  const index = ATTENDANCE_STATES.findIndex((state) => state.id === current);
  return ATTENDANCE_STATES[(index + 1) % ATTENDANCE_STATES.length].id;
}

function attendanceCountsHtml(counts) {
  return `
    <div class="att-counts">
      ${ATTENDANCE_STATES.map(
        (state) => `<span class="att-count ${state.id}">${state.icon} ${state.label}: <b>${counts[state.id] || 0}</b></span>`
      ).join("")}
    </div>`;
}

function formatBirthDate(value) {
  if (!value) return "";
  const [year, month, day] = String(value).split("-");
  return `${day}.${month}.${year}`;
}

// Profil tabidagi "Sinflarim" bo'limi — eslangan ro'yxat darhol, yangisi fonda
let myClassesCache = null;

function curatorLine(item) {
  if (item.is_curator) return "🎓 Siz — kurator";
  return item.curator ? `🎓 Kurator: ${escapeHtml(item.curator.name)}` : "⚠️ Kurator tayinlanmagan";
}

// Ro'yxatdagi kurator qatori: o'zi kurator bo'lsa — "Sizning sinfingiz"
function classCuratorNote(item) {
  if (item.is_curator) return "⭐ Sizning sinfingiz";
  return item.curator ? `🎓 Kurator: ${escapeHtml(item.curator.name)}` : "⚠️ Kurator tayinlanmagan";
}

/*
  Sinflar ro'yxati: chapda o'quvchilar soni, o'rtada sinf nomi va
  ostida kurator (o'zingizniki bo'lsa — "Sizning sinfingiz").
*/
function classListHtml(classes, scope = "mine") {
  if (!classes.length) {
    return `<p class="subtitle" style="margin:6px 0 0">${
      scope === "mine" ? "Siz hali hech qaysi sinfning kuratori emassiz." : "Maktabda hali sinf yo'q."
    }</p>`;
  }
  return `
    <div class="class-list">
      ${classes
        .map(
          (item) => `
            <button type="button" class="class-row${item.is_curator ? " is-mine" : ""}" data-class-id="${item.id}">
              <span class="class-count"><b>${item.students_count}</b><small>o'quvchi</small></span>
              <span class="class-info">
                <b>${escapeHtml(item.name)}</b>
                <small>${classCuratorNote(item)}</small>
              </span>
              <span class="class-arrow">›</span>
            </button>`
        )
        .join("")}
    </div>`;
}

function wireClassList(container) {
  container.querySelectorAll("[data-class-id]").forEach((item) => {
    item.addEventListener("click", guarded(() => renderClass(Number(item.dataset.classId), "students")));
  });
}

async function loadMyClassesInto(container, scope = "mine") {
  // "Mening sinflarim" — faqat kuratorligim; "Barcha sinflar" — hammasi (o'zimniki ham, ⭐ bilan)
  const pick = (classes) => (scope === "mine" ? classes.filter((item) => item.is_curator) : classes);
  const draw = (classes) => {
    container.innerHTML = classListHtml(pick(classes), scope);
    wireClassList(container);
    prefetchClassDetails(pick(classes));
  };
  if (myClassesCache) draw(myClassesCache);
  try {
    const classes = await api("/api/classes");
    const changed = JSON.stringify(classes) !== JSON.stringify(myClassesCache);
    myClassesCache = classes;
    if (changed && container.isConnected) draw(classes);
  } catch (error) {
    if (!myClassesCache && container.isConnected) container.innerHTML = `<p class="subtitle">${escapeHtml(error.message)}</p>`;
  }
}

function renderCreateClass() {
  showPage(`
    ${schoolPageHeader("Sinf yaratish", "back-profile")}
    <p class="subtitle">Sinf yaratgach, sozlamalardan (⚙️) unga kurator tayinlaysiz.</p>
    <label class="field-label" for="class-name-input">Sinf / guruh nomi</label>
    <input class="input" id="class-name-input" maxlength="100" placeholder="Masalan: 10/2, Iqtisod guruhi, Filologiya guruhi" />
    <button class="btn" id="create-class-button" style="margin-top:14px">➕ Yaratish</button>
  `);

  document.querySelector('[data-action="back-profile"]').addEventListener("click", () => renderSchoolClasses(currentSchool, "all"));
  document.getElementById("create-class-button").addEventListener(
    "click",
    guarded(async () => {
      const name = document.getElementById("class-name-input").value.trim();
      if (!name) throw new Error("Sinf nomini kiriting");
      const created = await api("/api/classes", { method: "POST", body: { name } });
      myClassesCache = null;
      showToast(`✅ «${created.name}» yaratildi — endi kurator tayinlang`);
      openClassTab(created, "students");
      openClassSettings(created, "students");
    })
  );
}

/* ---------- Sinf markazi: o'z pastki menyusi bilan ---------- */

/*
  Sinf ichida: 👨‍🎓 O'quvchilar · 📋 Davomat · 📊 Natijalar.
  O'quvchi sinfga BIR MARTA qo'shiladi — u davomatda ham, o'quvchi
  profilida ham (keyinchalik natijalarda ham) avtomatik bo'ladi.
*/
const CLASS_TABS = [
  { id: "students", icon: "👨‍🎓", label: "O'quvchilar" },
  { id: "attendance", icon: "📋", label: "Davomat" },
  { id: "results", icon: "📊", label: "Natijalar" },
];

let currentClass = null;

function showClassNav(activeTab) {
  let host = document.getElementById("class-nav-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "class-nav-host";
    host.className = "bottom-nav";
    host.innerHTML = CLASS_TABS.map(
      (tab) => `
        <button class="nav-item" type="button" data-class-nav="${tab.id}">
          <span class="nav-icon">${tab.icon}</span>
          <span>${tab.label}</span>
        </button>`
    ).join("");
    document.body.appendChild(host);

    host.addEventListener("click", (event) => {
      const button = event.target.closest("[data-class-nav]");
      if (!button || button.classList.contains("active") || !currentClass) return;
      const tab = button.dataset.classNav;
      openClassTab(currentClass, tab);
      refreshClassInBackground(tab);
    });
  }
  host.hidden = false;
  host.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.classNav === activeTab);
  });
}

/*
  Tab darhol eslangan ma'lumot bilan ochiladi, sinf esa fonda qayta so'raladi:
  boshqa o'qituvchi hozirgina davomat olgan yoki kurator o'quvchi qo'shgan
  bo'lishi mumkin. O'zgargan bo'lsa va hali shu tabda bo'lsak — yangilanadi.
*/
async function refreshClassInBackground(tab) {
  const before = currentClass;
  if (!before) return;
  try {
    const fresh = await api(`/api/classes/${before.id}`);
    const stillHere = document.querySelector(`[data-class-tab="${tab}"]`) && currentClass?.id === fresh.id;
    classDetailCache.set(fresh.id, fresh);
    if (stillHere && JSON.stringify(fresh) !== JSON.stringify(currentClass)) openClassTab(fresh, tab);
    else if (currentClass?.id === fresh.id) currentClass = fresh;
  } catch (_) {
    /* fonda — xato bo'lsa eslangan ma'lumot qoladi */
  }
}

/*
  Sinf ma'lumoti eslab qolinadi: qayta kirganda sinf DARHOL ochiladi (serverni
  kutmasdan), yangisi esa fonda so'raladi. Har qanday o'zgartirishdan keyin
  (POST/PUT/DELETE) eslab qolingani tozalanadi.
*/
function openClassTab(schoolClass, tab) {
  currentClass = schoolClass;
  classDetailCache.set(schoolClass.id, schoolClass);
  if (tab === "students") renderClassStudentsTab(schoolClass);
  else if (tab === "results") renderClassResultsTab(schoolClass);
  else renderClassAttendanceTab(schoolClass);
}

// Sinfni (yangi ma'lumot bilan) ochadi. Eski chaqiruvlar ham shu orqali ishlaydi.
async function renderClass(classOrId, tab = "attendance") {
  const id = typeof classOrId === "object" ? classOrId.id : classOrId;
  const cached = classDetailCache.get(id);
  if (cached) {
    openClassTab(cached, tab);
    refreshClassInBackground(tab);
    return;
  }
  const fresh = await api(`/api/classes/${id}`);
  openClassTab(fresh, tab);
}

// Ro'yxatdagi sinflar fonda oldindan yuklanadi — bosilganda darhol ochiladi
function prefetchClassDetails(classes) {
  classes
    .filter((item) => !classDetailCache.has(item.id))
    .slice(0, 12)
    .forEach((item) => {
      api(`/api/classes/${item.id}`)
        .then((detail) => classDetailCache.set(detail.id, detail))
        .catch(() => {});
    });
}

function classHeader(schoolClass, action = "") {
  return `
    <div class="page-header">
      <button class="back-button" type="button" data-action="back-classes" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(schoolClass.name)}</h2>
      ${action}
      ${schoolClass.is_director ? `<button type="button" class="icon-button" id="class-settings-button" aria-label="Sinf sozlamalari">⚙️</button>` : ""}
    </div>
    <p class="subtitle class-curator-line">${curatorLine(schoolClass)}</p>`;
}

function wireClassTab(schoolClass, tab) {
  showClassNav(tab);
  document.querySelector('[data-action="back-classes"]').addEventListener("click", () =>
    renderSchoolClasses(currentSchool, lastClassesScope)
  );
  document.getElementById("class-settings-button")?.addEventListener("click", () => openClassSettings(schoolClass, tab));
}

/* --- Pastdan ochiladigan oyna (bottom sheet) --- */

function openSheet(innerHtml, { onClose = null } = {}) {
  document.querySelector(".sheet-backdrop")?.remove();
  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";
  backdrop.innerHTML = `<div class="sheet" role="dialog"><div class="sheet-handle"></div>${innerHtml}</div>`;
  document.body.appendChild(backdrop);
  requestAnimationFrame(() => backdrop.classList.add("open"));

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    backdrop.classList.remove("open");
    setTimeout(() => backdrop.remove(), 200);
    onClose?.();
  };
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  backdrop.querySelectorAll("[data-sheet-close]").forEach((button) => button.addEventListener("click", close));
  return { sheet: backdrop.querySelector(".sheet"), close };
}

function openClassSettings(schoolClass, tab) {
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 10px">⚙️ Sinf sozlamalari</h3>
    <label class="field-label" for="sheet-class-name">Sinf / guruh nomi</label>
    <input class="input" id="sheet-class-name" maxlength="100" value="${escapeHtml(schoolClass.name)}" />
    <button class="btn" id="sheet-save-name" style="margin-top:10px">💾 Nomini saqlash</button>

    <label class="field-label" for="sheet-curator" style="margin-top:16px">🎓 Kurator (sinf rahbari)</label>
    <select id="sheet-curator">
      <option value="">— Kurator tayinlanmagan —</option>
      ${(currentSchool?.members || [])
        .filter((member) => member.role !== "owner")
        .map(
          (member) =>
            `<option value="${member.user_id}" ${schoolClass.curator?.user_id === member.user_id ? "selected" : ""}>${escapeHtml(member.name)} — ${subjectsText(member.subjects)}</option>`
        )
        .join("")}
    </select>
    <p class="subtitle" style="margin:4px 0 0">Kurator o'quvchilarni qo'shadi va sinfni boshqaradi.</p>
    <button class="btn" id="sheet-save-curator" style="margin-top:10px">🎓 Kuratorni saqlash</button>

    ${schoolClass.can_delete ? `<button class="btn btn-ghost" id="sheet-delete-class" style="margin-top:16px">🗑 Sinfni o'chirish</button>` : ""}
    <button class="btn btn-ghost" data-sheet-close style="margin-top:8px">Yopish</button>
  `);

  sheet.querySelector("#sheet-save-name").addEventListener(
    "click",
    guarded(async () => {
      const name = sheet.querySelector("#sheet-class-name").value.trim();
      if (!name) throw new Error("Sinf nomini kiriting");
      if (name !== schoolClass.name) {
        const updated = await api(`/api/classes/${schoolClass.id}`, { method: "PUT", body: { name } });
        myClassesCache = null;
        showToast(`✅ Nomi o'zgartirildi: ${updated.name}`);
        openClassTab(updated, tab);
      }
      close();
    })
  );

  sheet.querySelector("#sheet-save-curator").addEventListener(
    "click",
    guarded(async () => {
      const value = sheet.querySelector("#sheet-curator").value;
      const updated = await api(`/api/classes/${schoolClass.id}/curator`, {
        method: "PUT",
        body: { user_id: value ? Number(value) : null },
      });
      myClassesCache = null;
      showToast(updated.curator ? `✅ Kurator: ${updated.curator.name}` : "Kurator olib tashlandi");
      close();
      openClassTab(updated, tab);
    })
  );

  sheet.querySelector("#sheet-delete-class")?.addEventListener("click", () => {
    close();
    renderDeleteClass(schoolClass, tab);
  });
}

/*
  Sinfni o'chirish — tasodifan bo'lmasligi uchun 3 bosqich:
    1) ⚙️ → "Sinfni o'chirish";
    2) ogohlantirishni o'qib, pastdagi "Roziman" belgisini qo'yish
       (belgisiz "O'chirish" tugmasi ishlamaydi);
    3) yana bir bor "Aniq o'chirasizmi?" — faqat "Ha" bilan o'chadi.
*/
function renderDeleteClass(schoolClass, fromTab = "attendance") {
  const studentCount = schoolClass.students.length;
  const attendanceCount = schoolClass.attendances.length;

  showPage(`
    ${schoolPageHeader("Sinfni o'chirish", "back-class-tab")}
    <div class="danger-panel">
      <div style="font-size:40px;text-align:center">⚠️</div>
      <h3 style="text-align:center;margin:6px 0 12px">Diqqat! «${escapeHtml(schoolClass.name)}» sinfi o'chiriladi</h3>
      <p>O'chirilgandan keyin:</p>
      <ul>
        <li>sinf «Sinflarim» ro'yxatidan butunlay yo'qoladi;</li>
        <li><b>${studentCount} ta o'quvchi</b> ro'yxati va ularning profillari ko'rinmaydi;</li>
        <li><b>${attendanceCount} ta davomat</b> yozuvi va butun davomat tarixi ko'rinmaydi;</li>
        <li>bu sinf uchun yangi davomat olib bo'lmaydi.</li>
      </ul>
      <p><b>Bu amalni o'zingiz qaytara olmaysiz.</b> Xavfsizlik uchun ma'lumotlar serverda arxivda saqlanadi,
        ularni faqat platforma admini tiklay oladi.</p>
      <p>Faqat nomini o'zgartirmoqchi bo'lsangiz — o'chirmang, ⚙️ sozlamalardan nomini o'zgartiring.</p>
    </div>

    <label class="consent-row">
      <input type="checkbox" id="delete-consent" />
      <span>Ogohlantirishni o'qidim va «${escapeHtml(schoolClass.name)}» sinfini o'chirishga roziman</span>
    </label>
    <button class="btn btn-danger" id="confirm-delete-class" disabled style="margin-top:12px">🗑 Sinfni o'chirish</button>
    <button class="btn btn-ghost" id="cancel-delete-class" style="margin-top:8px">Bekor qilish</button>
  `);

  const back = () => openClassTab(schoolClass, fromTab);
  document.querySelector('[data-action="back-class-tab"]').addEventListener("click", back);
  document.getElementById("cancel-delete-class").addEventListener("click", back);

  const consent = document.getElementById("delete-consent");
  const deleteButton = document.getElementById("confirm-delete-class");
  consent.addEventListener("change", () => {
    deleteButton.disabled = !consent.checked;
  });

  deleteButton.addEventListener(
    "click",
    guarded(async () => {
      if (!consent.checked) return;
      const sure = await askConfirm(`Aniq o'chirasizmi? «${schoolClass.name}» sinfi ro'yxatingizdan butunlay olinadi.`);
      if (!sure) return;
      await api(`/api/classes/${schoolClass.id}`, { method: "DELETE" });
      myClassesCache = null;
      showToast(`🗑 «${schoolClass.name}» o'chirildi`);
      renderMySchool(currentSchool);
    })
  );
}

/* --- 📋 Davomat tabi: olish + tarix (kunlar bo'yicha) --- */

function dayLabel(isoString) {
  const date = parseUtc(isoString);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(date, today)) return "Bugun";
  if (same(date, yesterday)) return "Kecha";
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}

function dateLabel(isoString) {
  const date = parseUtc(isoString);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}

// Davomat qachon: "09.10.2026 · 11:00–11:45" — sana, keyin dars vaqti.
// Vaqt kiritilmagan eski davomatlarda — olingan vaqt.
function attendanceWhen(item) {
  const time = item.lesson_start && item.lesson_end ? `${item.lesson_start}–${item.lesson_end}` : timeLabel(item.taken_at);
  return `${dateLabel(item.taken_at)} · ${time}`;
}

function timeLabel(isoString) {
  const date = parseUtc(isoString);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Davomat tarixi — fan bo'yicha filtr (sinf id → tanlangan fan)
const attendanceSubjectFilter = new Map();

function renderClassAttendanceTab(schoolClass) {
  currentClass = schoolClass;
  // Bir nechta fan bo'lsa (kurator/rahbariyat ko'radi) — fan bo'yicha ajratib ko'rish
  const subjects = [...new Set(schoolClass.attendances.map((item) => item.subject || ""))];
  let chosen = attendanceSubjectFilter.get(schoolClass.id) ?? null;
  if (chosen !== null && !subjects.includes(chosen)) chosen = null;
  const visible = chosen === null ? schoolClass.attendances : schoolClass.attendances.filter((item) => (item.subject || "") === chosen);
  const filterHtml =
    subjects.length > 1
      ? `<div class="subject-filter">
           <button type="button" class="filter-chip${chosen === null ? " active" : ""}" data-subject-filter="__all">Hammasi</button>
           ${subjects
             .map(
               (subject) =>
                 `<button type="button" class="filter-chip${chosen === subject ? " active" : ""}" data-subject-filter="${escapeHtml(subject)}">${escapeHtml(subject || "Fansiz")}</button>`
             )
             .join("")}
         </div>`
      : "";

  const groups = [];
  visible.forEach((item) => {
    const label = dayLabel(item.taken_at);
    if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, items: [] });
    groups[groups.length - 1].items.push(item);
  });

  const historyHtml = groups.length
    ? groups
        .map(
          (group) => `
            <p class="section-label" style="margin-top:16px">${group.label.toUpperCase()}</p>
            <div class="panel pick-list">
              ${group.items
                .map(
                  (item) => `
                    <button type="button" class="pick-item" data-attendance-id="${item.id}">
                      <span class="pick-title">🕘 ${attendanceWhen(item)} · ${escapeHtml(lessonName(item.subject))}</span>
                      <span>›</span>
                    </button>`
                )
                .join("")}
            </div>`
        )
        .join("")
    : `<p class="subtitle" style="margin-top:14px">Hali davomat olinmagan. Birinchi davomatni oling!</p>`;

  showPage(`
    <div data-class-tab="attendance">
      ${classHeader(schoolClass)}
      <button class="btn att-take-button" id="take-attendance-button">📋 ${
        (currentSchool?.my_subjects || []).length === 1 ? `${escapeHtml(lessonName(currentSchool.my_subjects[0]))} davomati` : "Davomat olish"
      }</button>
      <p class="subtitle" style="margin:6px 0 0;text-align:center">👨‍🎓 ${schoolClass.students.length} ta o'quvchi · hamma oldindan "Bor"</p>
      ${filterHtml}
      ${historyHtml}
    </div>
  `);

  wireClassTab(schoolClass, "attendance");
  document.getElementById("take-attendance-button").addEventListener("click", () => {
    if (!schoolClass.students.length) {
      if (schoolClass.can_manage) {
        showToast("Avval sinfga o'quvchilarni qo'shing");
        openClassTab(schoolClass, "students");
        openAddStudentSheet(schoolClass);
      } else {
        showToast("Sinfda hali o'quvchi yo'q — ularni kurator qo'shadi");
      }
      return;
    }
    renderAttendanceForm(schoolClass);
  });
  document.querySelectorAll("[data-attendance-id]").forEach((item) => {
    item.addEventListener("click", guarded(() => renderAttendanceDetail(Number(item.dataset.attendanceId), schoolClass)));
  });
  document.querySelectorAll("[data-subject-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const value = chip.dataset.subjectFilter;
      attendanceSubjectFilter.set(schoolClass.id, value === "__all" ? null : value);
      renderClassAttendanceTab(schoolClass);
    });
  });
}

/* --- 👨‍🎓 O'quvchilar tabi --- */

// Jami (barcha darslar) — bugungi emasligi aniq yozilsin
function studentStatsLine(stats) {
  if (!stats || !stats.total) return "Hali davomat olinmagan";
  return `${stats.total} ta darsda: ✅ ${stats.present} · ❌ ${stats.absent} · 🟡 ${stats.excused} · ⏰ ${stats.late}`;
}

// O'quvchini tahrirlash oynasi — ro'yxatdan ham, profildan ham ochiladi
function openEditStudentSheet(schoolClass, student, onSaved) {
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 10px">✏️ O'quvchini tahrirlash</h3>
    <label class="field-label" for="sheet-edit-name">Ism-familiya</label>
    <input class="input" id="sheet-edit-name" maxlength="200" value="${escapeHtml(student.full_name)}" />
    <button class="btn" id="sheet-save-student" style="margin-top:10px">💾 Saqlash</button>
    <button class="btn btn-ghost" data-sheet-close style="margin-top:8px">Bekor qilish</button>
  `);

  const input = sheet.querySelector("#sheet-edit-name");
  setTimeout(() => {
    input.focus();
    input.select();
  }, 250);

  const save = guarded(async () => {
    const fullName = input.value.trim();
    if (fullName.length < 2) throw new Error("Ism-familiyani kiriting");
    await api(`/api/classes/${schoolClass.id}/students/${student.id}`, {
      method: "PUT",
      // Tug'ilgan sana hozircha so'ralmaydi — avval kiritilgan bo'lsa, o'chirib yubormaymiz
      body: { full_name: fullName, birth_date: student.birth_date || null },
    });
    myClassesCache = null;
    close();
    showToast("✅ Saqlandi");
    await onSaved();
  });
  sheet.querySelector("#sheet-save-student").addEventListener("click", save);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") save(event);
  });
}

function rateBadge(stats) {
  // Davomat hali olinmagan — belgi ko'rsatilmaydi ("Hali davomat olinmagan" yozuvi yetarli)
  if (!stats || stats.rate === null || stats.rate === undefined) return "";
  const level = stats.rate >= 90 ? "good" : stats.rate >= 70 ? "mid" : "low";
  return `<span class="rate-badge ${level}">${stats.rate}%</span>`;
}

function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  return escapeHtml(((parts[0] || "?").charAt(0) + (parts[1] || "").charAt(0)).toUpperCase());
}

function renderClassStudentsTab(schoolClass) {
  currentClass = schoolClass;
  const rows = schoolClass.students.length
    ? schoolClass.students
        .map(
          (student) => `
            <div class="student-card" data-student-id="${student.id}" role="button" tabindex="0">
              <span class="student-avatar">${initials(student.full_name)}</span>
              <span class="student-info">
                <b>${escapeHtml(student.full_name)}</b>
                <small>${studentStatsLine(student.stats)}</small>
              </span>
              ${rateBadge(student.stats)}
            </div>`
        )
        .join("")
    : `<div class="empty-state">
         <div style="font-size:42px">👨‍🎓</div>
         <p>Sinfda hali o'quvchi yo'q.</p>
         <p class="subtitle">${
           schoolClass.can_manage
             ? "Tepadagi \"➕ Qo'shish\" bilan o'quvchilarni qo'shing — bittalab yoki Excel'dan bir yo'la. Ular davomatda va boshqa bo'limlarda o'zi chiqadi."
             : "O'quvchilarni sinf kuratori qo'shadi."
         }</p>
       </div>`;

  showPage(`
    <div data-class-tab="students">
      ${classHeader(schoolClass, schoolClass.can_manage ? `<button type="button" class="share-pill" id="add-student-pill">➕ Qo'shish</button>` : "")}
      <p class="subtitle" style="margin:0 0 10px">${schoolClass.students.length} ta o'quvchi · % — barcha darslar bo'yicha qatnashish (bugungi emas)</p>
      <div class="student-list">${rows}</div>
    </div>
  `);

  wireClassTab(schoolClass, "students");
  document.getElementById("add-student-pill")?.addEventListener("click", () => openAddStudentSheet(schoolClass));
  document.querySelectorAll("[data-student-id]").forEach((card) => {
    card.addEventListener(
      "click",
      // Tahrirlash — o'quvchi profilining ichida
      guarded(async () => {
        await renderStudentProfile(schoolClass, Number(card.dataset.studentId));
      })
    );
  });
}

function openAddStudentSheet(schoolClass) {
  let addedCount = 0;
  // Oyna yopilganda (Tayyor yoki orqa fonga bosilsa) ro'yxat yangilanadi
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 8px">➕ O'quvchi qo'shish</h3>
    <div class="import-actions">
      <label class="btn btn-ghost import-file-label">
        📄 Excel'dan
        <input type="file" id="import-file" accept=".xlsx,.csv,.txt" hidden />
      </label>
      <button type="button" class="btn btn-ghost" id="open-paste">📋 Ro'yxatni joylash</button>
    </div>
    <p class="subtitle" style="margin:8px 0 8px">yoki bittalab yozing — har biridan keyin maydon tozalanadi:</p>
    <label class="field-label" for="sheet-student-name">Ism-familiya</label>
    <input class="input" id="sheet-student-name" maxlength="200" placeholder="Masalan: Islom Karimov" enterkeyhint="next" />
    <button class="btn" id="sheet-add-student" style="margin-top:10px">➕ Qo'shish</button>
    <button class="btn btn-ghost" data-sheet-close style="margin-top:8px">Tayyor</button>
    <p class="subtitle" id="sheet-added" style="margin:8px 0 0"></p>
  `, {
    onClose: () => {
      if (addedCount) openClassTab(currentClass, "students");
    },
  });

  const nameInput = sheet.querySelector("#sheet-student-name");
  setTimeout(() => nameInput.focus(), 250);

  const add = guarded(async () => {
    const fullName = nameInput.value.trim();
    if (fullName.length < 2) throw new Error("O'quvchining ism-familiyasini kiriting");
    const updated = await api(`/api/classes/${schoolClass.id}/students`, {
      method: "POST",
      body: { full_name: fullName },
    });
    myClassesCache = null;
    addedCount += 1;
    currentClass = updated;
    sheet.querySelector("#sheet-added").textContent = `✅ ${fullName} qo'shildi (jami: ${addedCount})`;
    nameInput.value = "";
    nameInput.focus();
  });

  sheet.querySelector("#sheet-add-student").addEventListener("click", add);

  // Tayyor ro'yxat: Excel/CSV fayl yoki nusxalab joylash
  sheet.querySelector("#import-file").addEventListener(
    "change",
    guarded(async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      close();
      await importStudentsFromFile(schoolClass, file);
    })
  );
  sheet.querySelector("#open-paste").addEventListener("click", () => {
    close();
    openPasteSheet(schoolClass);
  });
  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") add(event);
  });
  return close;
}

/* --- O'quvchilarni Excel yoki ro'yxatdan import qilish --- */

const IMPORT_MAX_FILE_BYTES = 3 * 1024 * 1024;

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",").pop());
    reader.onerror = () => reject(new Error("Faylni o'qib bo'lmadi"));
    reader.readAsDataURL(file);
  });
}

async function importStudentsFromFile(schoolClass, file) {
  if (!file) return;
  if (file.size > IMPORT_MAX_FILE_BYTES) throw new Error("Fayl juda katta (3 MB dan oshmasin)");
  const fileBase64 = await readFileAsBase64(file);
  const preview = await api(`/api/classes/${schoolClass.id}/students/import/preview`, {
    method: "POST",
    body: { file_base64: fileBase64, file_name: file.name },
  });
  showImportPreview(schoolClass, preview, `📄 ${file.name}`);
}

function openPasteSheet(schoolClass) {
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 4px">📋 Ro'yxatni joylash</h3>
    <p class="subtitle" style="margin:0 0 8px">
      Excel, Word yoki Telegram'dagi ro'yxatni nusxalab shu yerga joylang — har qatorda bitta o'quvchi.
      Raqamlar ("1.", "2)") va sarlavha o'zi olib tashlanadi.
    </p>
    <textarea id="paste-students" rows="9" placeholder="1. Karimov Islom&#10;2. Aliyeva Dilnoza&#10;3. Raximov Sardor"></textarea>
    <button class="btn" id="paste-preview" style="margin-top:10px">🔍 Ko'rib chiqish</button>
    <button class="btn btn-ghost" data-sheet-close style="margin-top:8px">Bekor qilish</button>
  `);
  const textarea = sheet.querySelector("#paste-students");
  setTimeout(() => textarea.focus(), 250);

  sheet.querySelector("#paste-preview").addEventListener(
    "click",
    guarded(async () => {
      const text = textarea.value.trim();
      if (!text) throw new Error("Ro'yxatni joylang");
      const preview = await api(`/api/classes/${schoolClass.id}/students/import/preview`, {
        method: "POST",
        body: { text },
      });
      close();
      showImportPreview(schoolClass, preview, "📋 Joylangan ro'yxat");
    })
  );
}

// Ko'rib chiqish: nima qo'shilishini ko'rsatadi — tasdiqlangandagina saqlanadi
function showImportPreview(schoolClass, preview, sourceLabel) {
  const names = preview.names;
  const skipped = preview.already_in_class;

  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 4px">${names.length ? `✅ ${names.length} ta o'quvchi topildi` : "Yangi o'quvchi topilmadi"}</h3>
    <p class="subtitle" style="margin:0 0 8px">${escapeHtml(sourceLabel)}</p>
    ${
      skipped.length
        ? `<p class="subtitle" style="margin:0 0 8px">ℹ️ ${skipped.length} tasi sinfda allaqachon bor — qayta qo'shilmaydi.</p>`
        : ""
    }
    <ol class="import-list">${names.map((name) => `<li>${escapeHtml(name)}</li>`).join("")}</ol>
    ${
      names.length
        ? `<button class="btn" id="import-confirm" style="margin-top:12px">➕ ${names.length} ta o'quvchini qo'shish</button>`
        : ""
    }
    <button class="btn btn-ghost" data-sheet-close style="margin-top:8px">Bekor qilish</button>
  `);

  sheet.querySelector("#import-confirm")?.addEventListener(
    "click",
    guarded(async () => {
      const result = await api(`/api/classes/${schoolClass.id}/students/bulk`, {
        method: "POST",
        body: { names },
      });
      myClassesCache = null;
      close();
      showToast(`✅ ${result.added} ta o'quvchi qo'shildi`);
      openClassTab(result.class, "students");
    })
  );
}

async function renderStudentProfile(schoolClass, studentId) {
  const student = await api(`/api/classes/${schoolClass.id}/students/${studentId}`);
  const stats = student.stats;

  const historyHtml = student.history.length
    ? student.history
        .map(
          (item) => `
            <div class="att-row" style="cursor:default">
              <span class="att-name">${dayLabel(item.taken_at)} · ${timeLabel(item.taken_at)}
                <small class="subtitle" style="display:block;margin:0">${escapeHtml(lessonName(item.subject))}</small>
              </span>
              <span class="att-status ${item.status}">${ATTENDANCE_STATE_BY_ID[item.status].icon} ${ATTENDANCE_STATE_BY_ID[item.status].label}</span>
            </div>`
        )
        .join("")
    : `<p class="subtitle" style="padding:12px 14px;margin:0">Hali davomat olinmagan.</p>`;

  showPage(`
    ${schoolPageHeader(student.full_name, "back-students")}
    <div class="card" style="cursor:default">
      <span class="student-avatar" style="width:56px;height:56px;font-size:20px">${initials(student.full_name)}</span>
      <div class="card-body" style="width:100%">
        <h3 style="margin:0">${escapeHtml(student.full_name)}</h3>
        <p style="margin:4px 0 0">📚 ${escapeHtml(student.class_name)}</p>
        <p class="subtitle" style="margin:2px 0 0">${stats.total ? `${stats.total} ta darsda davomat olingan` : "Hali davomat olinmagan"}</p>
      </div>
      ${rateBadge(stats)}
    </div>

    <div class="stats-grid" style="margin-top:12px">
      <div class="stat-tile"><span class="stat-value" style="color:#1e9e4a">${stats.present}</span>Bor</div>
      <div class="stat-tile"><span class="stat-value" style="color:#d93636">${stats.absent}</span>Yo'q</div>
      <div class="stat-tile"><span class="stat-value" style="color:#e0a800">${stats.excused}</span>Sababli</div>
      <div class="stat-tile"><span class="stat-value" style="color:#2e75b6">${stats.late}</span>Kechikdi</div>
    </div>

    ${
      schoolClass.can_manage
        ? `<div class="row-between" style="gap:8px;margin-top:12px">
             <button class="btn btn-ghost" id="edit-student-button" style="margin:0">✏️ Tahrirlash</button>
             <button class="btn btn-ghost" id="remove-student-button" style="margin:0">🚪 Sinfdan chiqarish</button>
           </div>`
        : ""
    }

    <p class="section-label" style="margin-top:18px">DARSLAR TARIXI (${stats.total})</p>
    <div class="panel" style="padding:4px 0">${historyHtml}</div>
  `);

  document.querySelector('[data-action="back-students"]').addEventListener("click", guarded(() => renderClass(schoolClass.id, "students")));

  document.getElementById("edit-student-button")?.addEventListener("click", () =>
    openEditStudentSheet(schoolClass, student, () => renderStudentProfile(schoolClass, student.id))
  );

  document.getElementById("remove-student-button")?.addEventListener(
    "click",
    guarded(async () => {
      if (!(await askConfirm(`${student.full_name} sinfdan chiqarilsinmi? Eski davomatlarda u saqlanib qoladi.`))) return;
      await api(`/api/classes/${schoolClass.id}/students/${student.id}`, { method: "DELETE" });
      myClassesCache = null;
      showToast("O'quvchi sinfdan chiqarildi");
      await renderClass(schoolClass.id, "students");
    })
  );
}

/* --- 📊 Natijalar tabi --- */

/*
  📊 Natijalar: o'qituvchi sinfda o'tkazgan test natijalarini kiritadi.
  Ko'radi — kiritgan o'qituvchi va rahbariyat (admin, direktor, zam direktor).
*/
async function renderClassResultsTab(schoolClass) {
  currentClass = schoolClass;
  showPage(`
    <div data-class-tab="results">
      ${classHeader(schoolClass)}
      <button class="btn" id="add-class-test-button">➕ Test natijasini kiritish</button>
      <div id="class-tests" style="margin-top:14px"><p class="subtitle">⏳ Yuklanmoqda...</p></div>
    </div>
  `);
  wireClassTab(schoolClass, "results");
  document.getElementById("add-class-test-button").addEventListener("click", () => {
    if (!schoolClass.students.length) {
      showToast("Avval sinfga o'quvchilarni qo'shing");
      return;
    }
    renderClassTestForm(schoolClass);
  });

  const holder = document.getElementById("class-tests");
  try {
    const tests = await api(`/api/classes/${schoolClass.id}/tests`);
    if (!holder.isConnected) return;
    holder.innerHTML = tests.length
      ? `<div class="panel pick-list">
           ${tests
             .map(
               (test) => `
                 <button type="button" class="pick-item" data-class-test="${test.id}">
                   <span class="pick-title">📝 ${escapeHtml(test.title)}
                     <small>${formatDate(test.taken_on)}${test.subject ? ` · ${escapeHtml(test.subject)}` : ""} · ${test.participants} ta o'quvchi · o'rtacha ${test.average_percent ?? 0}%</small>
                   </span>
                   <span>›</span>
                 </button>`
             )
             .join("")}
         </div>`
      : `<p class="subtitle" style="margin:6px 0 0">Hali natija kiritilmagan. Sinfda o'tkazgan testingiz natijalarini kiriting — har bir o'quvchining foizi va bahosi chiqadi.</p>`;
    holder.querySelectorAll("[data-class-test]").forEach((item) => {
      item.addEventListener("click", guarded(() => renderClassTestDetail(Number(item.dataset.classTest), schoolClass)));
    });
  } catch (error) {
    if (holder.isConnected) holder.innerHTML = `<p class="subtitle">${escapeHtml(error.message)}</p>`;
  }
}

function gradeBadge(grade) {
  if (!grade) return `<span class="grade-badge none">—</span>`;
  return `<span class="grade-badge g${grade}">${grade}</span>`;
}

async function sendClassTest(test, format) {
  if (telegram?.initData) {
    await api(`/api/class-tests/${test.id}/send?format=${format}`, { method: "POST", body: {} });
    showToast(`✅ ${format === "image" ? "Rasm" : "PDF"} bot chatiga yuborildi`);
  } else {
    await downloadFile(`/api/class-tests/${test.id}/file?format=${format}`, format === "image" ? "natija.png" : "natija.pdf");
  }
}

// Test natijalari: o'rin, ism, ball, foiz, baho — eng ko'p yechganlar tepada
async function renderClassTestDetail(testId, schoolClass, onBack = null) {
  const test = await api(`/api/class-tests/${testId}`);
  const medal = { 1: "🥇", 2: "🥈", 3: "🥉" };

  showPage(`
    ${schoolPageHeader(test.title, "back-results")}
    <div class="card" style="cursor:default;display:block">
      <div class="row-between" style="align-items:flex-start">
        <div>
          <b>📝 ${escapeHtml(test.title)}</b>
          <p class="subtitle" style="margin:4px 0 0">📅 ${formatDate(test.taken_on)}${test.subject ? ` · ${escapeHtml(test.subject)}` : ""} · maks. ${test.max_score} ball</p>
          <p class="subtitle" style="margin:2px 0 0">👤 Kiritgan: <b>${escapeHtml(test.teacher_name)}</b></p>
          <p class="subtitle" style="margin:2px 0 0">👥 Qatnashdi: ${test.participants} / ${test.total}</p>
        </div>
        <div class="rate-badge-lg ${(test.average_percent ?? 0) >= 86 ? "good" : (test.average_percent ?? 0) >= 56 ? "mid" : "low"}">${test.average_percent ?? 0}%<small>o'rtacha</small></div>
      </div>
    </div>

    <div class="panel score-table">
      <div class="score-row score-head"><span>#</span><span>O'quvchi</span><span>Ball</span><span>%</span><span>Baho</span></div>
      ${test.scores
        .map(
          (row) => `
            <div class="score-row${row.score === null ? " is-absent" : ""}">
              <span>${row.rank ? medal[row.rank] || row.rank : "—"}</span>
              <span class="score-name">${escapeHtml(row.name)}</span>
              <span>${row.score === null ? "—" : row.score}</span>
              <span>${row.percent === null ? "—" : `${row.percent}%`}</span>
              <span>${row.score === null ? `<small>kelmadi</small>` : gradeBadge(row.grade)}</span>
            </div>`
        )
        .join("")}
    </div>
    <p class="subtitle" style="margin:8px 4px 0">Baho: 86%+ — 5, 71%+ — 4, 56%+ — 3, qolgani — 2.</p>

    <p class="section-label" style="margin-top:18px">YUBORISH VA CHOP ETISH</p>
    <div class="contact-actions">
      <button class="btn btn-ghost" id="class-test-image">🖼 Rasm</button>
      <button class="btn btn-ghost" id="class-test-pdf">📄 PDF</button>
    </div>
    <p class="subtitle" style="margin:6px 4px 0">Bot sizning chatingizga yuboradi — u yerdan guruhga uzatasiz yoki chop etasiz.</p>

    ${test.can_edit ? `<button class="btn" id="edit-class-test" style="margin-top:16px">✏️ Tahrirlash</button>` : ""}
    ${test.can_edit ? `<button class="btn btn-ghost danger-text" id="delete-class-test">🗑 Natijalarni o'chirish</button>` : ""}
  `);

  const back = onBack || (() => renderClass(schoolClass.id, "results"));
  document.querySelector('[data-action="back-results"]').addEventListener("click", guarded(back));
  document.getElementById("class-test-image").addEventListener("click", guarded(() => sendClassTest(test, "image")));
  document.getElementById("class-test-pdf").addEventListener("click", guarded(() => sendClassTest(test, "pdf")));
  document.getElementById("edit-class-test")?.addEventListener("click", () => renderClassTestForm(schoolClass, test));
  document.getElementById("delete-class-test")?.addEventListener(
    "click",
    guarded(async () => {
      if (!(await askConfirm(`«${test.title}» natijalari o'chirilsinmi?`))) return;
      await api(`/api/class-tests/${test.id}`, { method: "DELETE" });
      showToast("🗑 Natijalar o'chirildi");
      await back();
    })
  );
}

/*
  Rahbariyat uchun: barcha sinflarning test natijalari. Sinf tanlanadi —
  o'sha sinfda qaysi o'qituvchi test natijasi kiritgan bo'lsa, hammasi ko'rinadi.
*/
async function renderSchoolResults() {
  const classes = await api("/api/schools/test-results");
  showPage(`
    ${schoolSubHeader("Test natijalari")}
    <p class="subtitle" style="margin:0 0 12px">Sinfni tanlang — undagi barcha o'qituvchilarning test natijalari chiqadi.</p>
    ${
      classes.length
        ? `<div class="class-list">
             ${classes
               .map(
                 (item) => `
                   <button type="button" class="class-row" data-results-class="${item.id}">
                     <span class="class-count"><b>${item.tests}</b><small>test</small></span>
                     <span class="class-info">
                       <b>${escapeHtml(item.name)}</b>
                       <small>${item.tests ? `O'rtacha ${item.average_percent ?? 0}% · oxirgisi ${formatDate(item.last_date)}` : "Hali natija kiritilmagan"}</small>
                     </span>
                     <span class="class-arrow">›</span>
                   </button>`
               )
               .join("")}
           </div>`
        : `<p class="subtitle">Maktabda hali sinf yo'q.</p>`
    }
  `);
  wireSchoolSubPage();
  document.querySelectorAll("[data-results-class]").forEach((item) => {
    item.addEventListener("click", guarded(() => renderSchoolClassTests(Number(item.dataset.resultsClass))));
  });
}

async function renderSchoolClassTests(classId) {
  const [schoolClass, tests] = await Promise.all([api(`/api/classes/${classId}`), api(`/api/classes/${classId}/tests`)]);
  showPage(`
    ${schoolPageHeader(`${schoolClass.name} — natijalar`, "back-school-results")}
    ${
      tests.length
        ? `<div class="panel pick-list">
             ${tests
               .map(
                 (test) => `
                   <button type="button" class="pick-item" data-class-test="${test.id}">
                     <span class="pick-title">📝 ${escapeHtml(test.title)}
                       <small>${formatDate(test.taken_on)}${test.subject ? ` · ${escapeHtml(test.subject)}` : ""} · 👤 ${escapeHtml(test.teacher_name)} · o'rtacha ${test.average_percent ?? 0}%</small>
                     </span>
                     <span>›</span>
                   </button>`
               )
               .join("")}
           </div>`
        : `<p class="subtitle">Bu sinfda hali test natijasi kiritilmagan.</p>`
    }
  `);
  document.querySelector('[data-action="back-school-results"]').addEventListener("click", guarded(renderSchoolResults));
  document.querySelectorAll("[data-class-test]").forEach((item) => {
    item.addEventListener(
      "click",
      guarded(() => renderClassTestDetail(Number(item.dataset.classTest), schoolClass, () => renderSchoolClassTests(classId)))
    );
  });
}

// Natija kiritish: test nomi, fan, sana, maksimal ball va har o'quvchining bali
function renderClassTestForm(schoolClass, existing = null) {
  const subjects = currentSchool?.my_subjects || [];
  const scoreOf = Object.fromEntries((existing?.scores || []).map((row) => [row.student_id, row.score]));
  const today = new Date().toISOString().slice(0, 10);

  showPage(`
    ${schoolPageHeader(existing ? "Natijalarni tahrirlash" : `Test natijasi — ${schoolClass.name}`, "back-results")}
    <label class="field-label" for="ct-title">Test nomi <span class="required-mark">*</span></label>
    <input class="input" id="ct-title" maxlength="200" placeholder="Masalan: 1-chorak nazorat ishi" value="${escapeHtml(existing?.title || "")}" />

    <div class="two-fields">
      <div>
        <label class="field-label" for="ct-subject">Fan</label>
        <input class="input" id="ct-subject" maxlength="100" list="ct-subjects" value="${escapeHtml(existing?.subject ?? subjects[0] ?? "")}" />
        <datalist id="ct-subjects">${schoolSubjectOptions().map((item) => `<option value="${escapeHtml(item)}"></option>`).join("")}</datalist>
      </div>
      <div>
        <label class="field-label" for="ct-date">Sana</label>
        <input class="input" id="ct-date" type="date" max="${today}" value="${existing ? String(existing.taken_on).slice(0, 10) : today}" />
      </div>
    </div>

    <label class="field-label" for="ct-max">Maksimal ball <span class="required-mark">*</span></label>
    <input class="input" id="ct-max" type="number" inputmode="decimal" min="1" max="1000" placeholder="Masalan: 30" value="${existing?.max_score ?? ""}" />

    <p class="section-label" style="margin:18px 0 6px">O'QUVCHILAR BALLI</p>
    <p class="subtitle" style="margin:0 0 8px">Testda qatnashmagan o'quvchining balini bo'sh qoldiring.</p>
    <div class="panel" style="padding:4px 0">
      ${schoolClass.students
        .map(
          (student, index) => `
            <div class="att-row score-input-row" style="cursor:default">
              <span class="att-number">${index + 1}</span>
              <span class="att-name">${escapeHtml(student.full_name)}</span>
              <input class="input score-input" type="number" inputmode="decimal" min="0" step="any" max="${existing?.max_score ?? ""}"
                     data-student="${student.id}" value="${scoreOf[student.id] ?? ""}" placeholder="—" />
            </div>`
        )
        .join("")}
    </div>
    <div class="att-save-bar">
      <button class="btn" id="save-class-test" style="margin:0">💾 Saqlash</button>
    </div>
  `);

  markUnsaved();
  document.querySelector('[data-action="back-results"]').addEventListener(
    "click",
    guarded(async () => {
      if (!(await confirmLeave())) return;
      if (existing) await renderClassTestDetail(existing.id, schoolClass);
      else await renderClass(schoolClass.id, "results");
    })
  );

  // Ball maksimaldan oshib ketmasin: yozayotgandayoq to'xtatiladi
  const maxInput = document.getElementById("ct-max");
  const limitScore = (input) => {
    const max = Number(maxInput.value);
    const value = Number(input.value.replace(",", "."));
    if (max > 0 && input.value !== "" && value > max) {
      input.value = String(max);
      input.classList.add("is-over");
      setTimeout(() => input.classList.remove("is-over"), 900);
      showToast(`Ball ${max} dan oshmaydi`);
    }
    if (input.value !== "" && value < 0) input.value = "0";
  };
  document.querySelectorAll(".score-input").forEach((input) => {
    input.addEventListener("input", () => limitScore(input));
  });
  maxInput.addEventListener("input", () => {
    document.querySelectorAll(".score-input").forEach((input) => {
      input.max = maxInput.value || "";
      limitScore(input);
    });
  });

  document.getElementById("save-class-test").addEventListener(
    "click",
    guarded(async () => {
      const title = document.getElementById("ct-title").value.trim();
      if (title.length < 2) throw new Error("Test nomini kiriting");
      const maxScore = Number(document.getElementById("ct-max").value);
      if (!maxScore || maxScore <= 0) throw new Error("Maksimal ballni kiriting");

      const scores = {};
      for (const input of document.querySelectorAll(".score-input")) {
        const raw = input.value.trim().replace(",", ".");
        if (raw === "") continue;
        const value = Number(raw);
        if (Number.isNaN(value) || value < 0 || value > maxScore) {
          input.focus();
          throw new Error(`Ball 0 dan ${maxScore} gacha bo'lishi kerak`);
        }
        scores[input.dataset.student] = value;
      }
      if (!Object.keys(scores).length) throw new Error("Kamida bitta o'quvchining balini kiriting");

      const body = {
        title,
        subject: document.getElementById("ct-subject").value.trim(),
        taken_on: document.getElementById("ct-date").value || today,
        max_score: maxScore,
        scores,
      };
      const saved = existing
        ? await api(`/api/class-tests/${existing.id}`, { method: "PUT", body })
        : await api(`/api/classes/${schoolClass.id}/tests`, { method: "POST", body });

      unsavedChanges = false;
      setExitProtection(false);
      showToast("✅ Natijalar saqlandi");
      await renderClassTestDetail(saved.id, schoolClass);
    })
  );
}

function lessonName(subject) {
  return subject ? `${subject} darsi` : "Dars";
}

/*
  Davomat o'qituvchi profilidagi fan bo'yicha olinadi — har safar tanlash
  shart emas. Bitta fan bo'lsa, u avtomatik ("Matematika darsi"); bir nechta
  bo'lsa — "Qaysi dars?" deb tanlanadi (birinchisi oldindan tanlangan).
*/
function attendanceSubjectFieldHtml(current = "") {
  const subjects = currentSchool?.my_subjects || [];
  const chosen = current || subjects[0] || "";

  if (subjects.length <= 1 && (!current || subjects.includes(current))) {
    return `
      <input type="hidden" id="attendance-subject" value="${escapeHtml(chosen)}" />
      <div class="lesson-chip">📚 ${escapeHtml(lessonName(chosen))}</div>`;
  }

  const options = subjects.includes(chosen) ? subjects : [chosen, ...subjects];
  return `
    <label class="field-label" for="attendance-subject">Qaysi dars?</label>
    <select id="attendance-subject">
      ${options.map((subject) => `<option value="${escapeHtml(subject)}" ${subject === chosen ? "selected" : ""}>${escapeHtml(lessonName(subject))}</option>`).join("")}
    </select>`;
}

// Yangi davomat uchun dars vaqti taklifi: hozirgi vaqt (5 daqiqaga
// yaxlitlab) va 45 daqiqalik dars. O'qituvchi o'zgartira oladi.
function defaultLessonTime() {
  const pad = (value) => String(value).padStart(2, "0");
  const now = new Date();
  const startMinutes = now.getHours() * 60 + Math.floor(now.getMinutes() / 5) * 5;
  const endMinutes = Math.min(startMinutes + 45, 23 * 60 + 59);
  const clock = (minutes) => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
  return { start: clock(startMinutes), end: clock(endMinutes) };
}

/*
  Davomat oynasi. Yangi davomatda hamma oldindan "Bor"; tahrirda saqlangan
  holatlar. O'quvchi qatori bosilganda holat almashadi:
  Bor → Yo'q → Sababli → Kechikdi → Bor.
*/
function renderAttendanceForm(schoolClass, existing = null) {
  const rows = existing
    ? existing.marks.map((mark) => ({ id: mark.student_id, name: mark.name, status: mark.status }))
    : schoolClass.students.map((student) => ({ id: student.id, name: student.full_name, status: "present" }));

  const counts = () =>
    rows.reduce((result, row) => ({ ...result, [row.status]: (result[row.status] || 0) + 1 }), {});

  const timesHtml = existing
    ? `<p class="subtitle" style="margin:0">📅 ${dateLabel(existing.taken_at)}${
        existing.updated_at ? ` · ✏️ Tahrirlangan: ${formatDateTime(existing.updated_at)}` : ""
      }</p>`
    : `<p class="subtitle" style="margin:0">📅 ${dateLabel(new Date().toISOString().slice(0, 19))}</p>`;
  const lesson = existing?.lesson_start ? { start: existing.lesson_start, end: existing.lesson_end } : defaultLessonTime();

  showPage(`
    ${schoolPageHeader(existing ? "Davomatni tahrirlash" : `Davomat — ${schoolClass.name}`, "back-class")}
    ${timesHtml}
    ${attendanceSubjectFieldHtml(existing ? existing.subject : "")}
    <div class="lesson-time">
      <label>Dars boshlandi<input type="time" class="input" id="lesson-start" value="${lesson.start}" required /></label>
      <span class="lesson-time-dash">—</span>
      <label>Dars tugadi<input type="time" class="input" id="lesson-end" value="${lesson.end}" required /></label>
    </div>
    <div id="att-counts-host">${attendanceCountsHtml(counts())}</div>
    <p class="subtitle" style="margin:4px 0 8px">O'quvchini bosing: Bor → Yo'q → Sababli → Kechikdi</p>
    <div class="panel" style="padding:4px 0">
      ${rows
        .map(
          (row, index) => `
            <button type="button" class="att-row" data-row="${index}">
              <span class="att-number">${index + 1}</span>
              <span class="att-name">${escapeHtml(row.name)}</span>
              <span class="att-status ${row.status}">${ATTENDANCE_STATE_BY_ID[row.status].icon} ${ATTENDANCE_STATE_BY_ID[row.status].label}</span>
            </button>`
        )
        .join("")}
    </div>
    <div class="att-save-bar">
      <button class="btn" id="save-attendance-button" style="margin:0">💾 Saqlash</button>
    </div>
  `);

  markUnsaved(); // davomat saqlanmaguncha tasodifan chiqib ketishdan himoya
  document.querySelector('[data-action="back-class"]').addEventListener(
    "click",
    guarded(async () => {
      if (!(await confirmLeave())) return;
      if (existing) await renderAttendanceDetail(existing.id, schoolClass);
      else await renderClass(schoolClass.id);
    })
  );

  document.querySelectorAll(".att-row").forEach((button) => {
    button.addEventListener("click", () => {
      const row = rows[Number(button.dataset.row)];
      row.status = nextAttendanceState(row.status);
      const state = ATTENDANCE_STATE_BY_ID[row.status];
      const pill = button.querySelector(".att-status");
      pill.className = `att-status ${row.status}`;
      pill.textContent = `${state.icon} ${state.label}`;
      document.getElementById("att-counts-host").innerHTML = attendanceCountsHtml(counts());
      telegram?.HapticFeedback?.selectionChanged?.();
    });
  });

  document.getElementById("save-attendance-button").addEventListener(
    "click",
    guarded(async () => {
      const subject = document.getElementById("attendance-subject").value.trim();
      const lessonStart = document.getElementById("lesson-start").value;
      const lessonEnd = document.getElementById("lesson-end").value;
      if (!lessonStart || !lessonEnd) throw new Error("Dars qachon boshlanib, qachon tugaganini kiriting");
      if (lessonEnd <= lessonStart) throw new Error("Dars tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak");

      const marks = Object.fromEntries(rows.map((row) => [row.id, row.status]));
      const body = { subject, marks, lesson_start: lessonStart, lesson_end: lessonEnd };
      const saved = existing
        ? await api(`/api/attendance/${existing.id}`, { method: "PUT", body })
        : await api(`/api/classes/${schoolClass.id}/attendance`, { method: "POST", body });

      unsavedChanges = false;
      setExitProtection(false);
      renderAttendanceShare(saved, schoolClass, { justSaved: true });
    })
  );
}

async function downloadFile(path, fileName) {
  const response = await fetch(path, { headers: { "X-Init-Data": telegram?.initData || "", "X-Dev-User": DEV_USER_ID } });
  if (!response.ok) throw new Error("Faylni yuklab bo'lmadi");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = fileName;
  link.click();
}

async function sendAttendance(attendance, format) {
  if (format === "text") {
    // Matn — Telegram'ning ulashish oynasi orqali to'g'ridan-to'g'ri ota-onalar guruhiga
    const { text } = await api(`/api/attendance/${attendance.id}/text`);
    if (telegram?.openTelegramLink) {
      openExternalLink(`https://t.me/share/url?url=${encodeURIComponent(" ")}&text=${encodeURIComponent(text)}`);
    } else {
      await copyToClipboard(text, "📋 Davomat matni nusxalandi");
    }
    return;
  }

  // Rasm va PDF — bot sizning chatingizga yuboradi, u yerdan guruhga "Uzatish" qilasiz
  if (telegram?.initData) {
    await api(`/api/attendance/${attendance.id}/send?format=${format}`, { method: "POST", body: {} });
    showToast(`✅ ${format === "image" ? "Rasm" : "PDF"} bot chatiga yuborildi`);
  } else {
    await downloadFile(`/api/attendance/${attendance.id}/file?format=${format}`, format === "image" ? "davomat.png" : "davomat.pdf");
  }
}

function renderAttendanceShare(attendance, schoolClass, { justSaved = false } = {}) {
  showPage(`
    ${schoolPageHeader(justSaved ? "Davomat saqlandi" : "Davomatni yuborish", "back-class")}
    ${justSaved ? `<div class="result-number pop">✅</div>` : ""}
    <div class="card" style="cursor:default;display:block">
      <b>${escapeHtml(schoolClass.name)} · ${escapeHtml(lessonName(attendance.subject))}</b>
      <p class="subtitle" style="margin:4px 0 0">🕘 ${attendanceWhen(attendance)}${
        attendance.updated_at ? ` · ✏️ ${formatDateTime(attendance.updated_at)}` : ""
      }</p>
      ${attendanceCountsHtml(attendance.counts)}
    </div>
    <p class="section-label" style="margin-top:18px">QANDAY YUBORASIZ?</p>
    ${roleCard("send-image", "🖼", "RASM", "Barcha o'quvchilar — jadval ko'rinishida")}
    ${roleCard("send-text", "📝", "MATN", "Faqat yo'q, sababli va kechikkanlar")}
    ${roleCard("send-pdf", "📄", "PDF", "Barcha o'quvchilar — printerga ham qulay")}
    <button class="btn btn-ghost" id="share-later" style="margin-top:12px">Keyinroq</button>
  `);

  const back = () => renderClass(schoolClass.id);
  document.querySelector('[data-action="back-class"]').addEventListener("click", guarded(back));
  document.getElementById("share-later").addEventListener("click", guarded(back));
  [["send-image", "image"], ["send-text", "text"], ["send-pdf", "pdf"]].forEach(([action, format]) => {
    document.querySelector(`[data-action="${action}"]`).addEventListener("click", guarded(() => sendAttendance(attendance, format)));
  });
}

async function renderAttendanceDetail(attendanceId, schoolClass) {
  const attendance = await api(`/api/attendance/${attendanceId}`);
  const total = attendance.marks.length;
  // Kechikkan ham darsda bo'lgan hisoblanadi
  const attended = attendance.counts.present + attendance.counts.late;
  const rate = total ? Math.round((attended / total) * 100) : 0;

  const group = (status, title) => {
    const names = attendance.marks.filter((mark) => mark.status === status);
    if (!names.length) return "";
    const state = ATTENDANCE_STATE_BY_ID[status];
    return `
      <p class="section-label" style="margin:16px 0 6px">${state.icon} ${title} — ${names.length}</p>
      <div class="panel" style="padding:4px 0">
        ${names
          .map(
            (mark, index) => `
              <div class="att-row" style="cursor:default">
                <span class="att-number">${index + 1}</span>
                <span class="att-name">${escapeHtml(mark.name)}</span>
              </div>`
          )
          .join("")}
      </div>`;
  };

  showPage(`
    ${schoolPageHeader(`Davomat — ${schoolClass.name}`, "back-history")}
    <div class="card" style="cursor:default;display:block">
      <div class="row-between" style="align-items:flex-start">
        <div>
          <b>📚 ${escapeHtml(lessonName(attendance.subject))}</b>
          <p class="subtitle" style="margin:4px 0 0">🕘 ${attendanceWhen(attendance)}</p>
          ${attendance.teacher_name ? `<p class="subtitle" style="margin:2px 0 0">👤 O'qituvchi: <b>${escapeHtml(attendance.teacher_name)}</b></p>` : ""}
          ${attendance.updated_at ? `<p class="subtitle" style="margin:2px 0 0">✏️ Tahrirlangan: ${formatDateTime(attendance.updated_at)}</p>` : ""}
        </div>
        <div class="rate-badge-lg ${rate >= 90 ? "good" : rate >= 70 ? "mid" : "low"}">${rate}%<small>qatnashdi</small></div>
      </div>
      ${attendanceCountsHtml(attendance.counts)}
    </div>
    ${group("absent", "YO'Q")}
    ${group("excused", "SABABLI")}
    ${group("late", "KECHIKDI")}
    ${group("present", "BOR")}
    ${attendance.can_edit ? `<button class="btn" id="edit-attendance-button" style="margin-top:16px">✏️ Tahrirlash</button>` : ""}
    <button class="btn btn-ghost" id="share-attendance-button" style="margin-top:10px">📤 Yuborish</button>
    ${attendance.can_edit ? `<button class="btn btn-ghost danger-text" id="delete-attendance-button">🗑 Davomatni o'chirish</button>` : ""}
  `);

  document.querySelector('[data-action="back-history"]').addEventListener(
    "click",
    guarded(() => renderClass(schoolClass.id, "attendance"))
  );
  document.getElementById("edit-attendance-button")?.addEventListener("click", () => renderAttendanceForm(schoolClass, attendance));
  document.getElementById("share-attendance-button").addEventListener("click", () => renderAttendanceShare(attendance, schoolClass));
  document.getElementById("delete-attendance-button")?.addEventListener(
    "click",
    guarded(async () => {
      const sure = await askConfirm(
        `${attendanceWhen(attendance)} dagi davomat o'chirilsinmi? O'quvchilarning qatnashish foizidan ham olib tashlanadi.`
      );
      if (!sure) return;
      await api(`/api/attendance/${attendance.id}`, { method: "DELETE" });
      myClassesCache = null;
      showToast("🗑 Davomat o'chirildi");
      await renderClass(schoolClass.id, "attendance");
    })
  );
}

function renderComingSoon(title, icon) {
  // Ta'lim muassasalari bo'limlari — mazmuni keyinroq qo'shiladi
  showPage(`
    <div class="page-header">
      <button class="back-button" type="button" data-action="home" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(title)}</h2>
    </div>
    <div class="card" style="cursor:default">
      <div class="card-icon">${icon}</div>
      <div class="card-body">
        <h3>Tez orada</h3>
        <p>Bu bo'lim ustida ishlanmoqda va yaqin orada ishga tushadi.</p>
      </div>
    </div>
    ${supportCardHtml("Bu bo'lim bo'yicha taklifingiz bo'lsa — yozing")}
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", renderSchoolFolder);
}

/* ---------- Xususiy maktab ---------- */

// Maktab profilida tanlanadigan fanlar (test fanlari ro'yxatidan, "Boshqa fan"siz)
function schoolSubjectOptions() {
  return SUBJECTS.filter((subject) => subject !== "Boshqa fan");
}

let cachedRegions = null;

async function loadRegions() {
  if (!cachedRegions) cachedRegions = await api("/api/regions");
  return cachedRegions;
}

function schoolPageHeader(title, action) {
  return `
    <div class="page-header">
      <button class="back-button" type="button" data-action="${action}" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(title)}</h2>
    </div>`;
}

/*
  "Xususiy maktab"ga kirish darhol bo'lsin (orqaga chiqish kabi tez).

  Ilgari har bosishda server "maktabim bormi?" deb so'ralardi va sahifa 1–2
  soniya kechikardi. Endi bu ma'lumot ilova ochilishi bilan fonda oldindan
  yuklanadi va eslab qolinadi: bosilganda sahifa shu zahoti chiziladi,
  tekshiruv esa fonda — holat o'zgargan bo'lsa (masalan, boshqa qurilmada
  qo'shilgan), sahifa jimgina yangilanadi.
*/
let schoolMembershipKnown = false;

async function prefetchSchoolData() {
  try {
    const { school } = await api("/api/schools/me");
    currentSchool = school;
    schoolMembershipKnown = true;
  } catch (_) {
    /* fonda — bo'lmasa, kirishda odatdagidek so'raladi */
  }
  loadRegions().catch(() => {}); // viloyatlar ro'yxati ham tayyor tursin
}

async function enterPrivateSchool() {
  if (schoolMembershipKnown) {
    // Darhol — eslangan holat bo'yicha
    if (currentSchool) renderMySchool(currentSchool);
    else {
      renderPrivateSchoolStart();
      // Fonda tekshiramiz: boshqa joyda maktabga qo'shilgan bo'lishi mumkin
      api("/api/schools/me")
        .then(({ school }) => {
          if (school && document.querySelector('[data-action="school-register"]')) renderMySchool(school, { refresh: false });
        })
        .catch(() => {});
    }
    return;
  }

  // Birinchi marta (oldindan yuklash ulgurmagan bo'lsa)
  const { school } = await api("/api/schools/me");
  currentSchool = school;
  schoolMembershipKnown = true;
  if (school) renderMySchool(school, { refresh: false });
  else renderPrivateSchoolStart();
}

/*
  Xususiy maktab — ikki yo'l:
    🔑 Maktabga kirish — maktab allaqachon ochilgan: viloyat → maktab → kod
       (kodni admindan so'raladi, bot ikkala tomonga tushuntiradi);
    📝 Maktabni ro'yxatdan o'tkazish — viloyat → (shu viloyatdagi maktablar
       ko'rinib turadi, o'ziniki bo'lsa qo'shiladi) → tuman → maktab ochish.
*/
function renderPrivateSchoolStart() {
  showPage(`
    ${schoolPageHeader("Xususiy maktab", "back-folder")}
    <div id="own-school-return"></div>
    ${roleCard("school-register", "📝", "MAKTABNI RO'YXATDAN O'TKAZISH", "Maktabingizni platformada birinchi bo'lib oching")}
    <div class="card" style="cursor:default;margin-top:12px">
      <div class="card-icon">✉️</div>
      <div class="card-body">
        <h3 style="margin:0">O'qituvchimisiz?</h3>
        <p style="margin:4px 0 0">Maktabga faqat admin qo'shadi: u sizga taklif havolasini yuboradi — havolani bosib, kodsiz qo'shilasiz.</p>
      </div>
    </div>
  `);

  // O'zi ochgan maktabdan chiqib ketgan admin — bir bosishda qaytadi
  api("/api/schools/me")
    .then(({ owned_school: owned }) => {
      const holder = document.getElementById("own-school-return");
      if (!owned || !holder) return;
      holder.innerHTML = roleCard("own-school", "🏫", "MAKTABIMGA QAYTISH", `«${escapeHtml(owned.name)}» — siz ochgan maktab`);
      holder.querySelector('[data-action="own-school"]').addEventListener(
        "click",
        guarded(() => renderOwnSchoolReturn(owned, renderPrivateSchoolStart))
      );
    })
    .catch(() => {});

  document.querySelector('[data-action="back-folder"]').addEventListener("click", renderSchoolFolder);
  document.querySelector('[data-action="school-register"]').addEventListener("click", guarded(() => renderCreateSchool()));
}

/*
  Viloyat va tuman — ikkita ochiluvchi ro'yxat (select). Tuman ro'yxati faqat
  viloyat tanlangach ochiladi va faqat o'sha viloyatning tumanlarini ko'rsatadi.
  Oxirgi tanlov eslab qolinadi: orqaga qaytilganda qayta tanlash shart emas.
*/
let lastSchoolPlace = { region: "", district: "" };

function locationSelectsHtml({ allDistricts = false } = {}) {
  return `
    <label class="field-label" for="place-region">Viloyat</label>
    <select id="place-region"><option value="">Viloyatni tanlang</option></select>
    <label class="field-label" for="place-district">Tuman / shahar</label>
    <select id="place-district" disabled>
      <option value="">${allDistricts ? "Barcha tumanlar" : "Avval viloyatni tanlang"}</option>
    </select>`;
}

async function wireLocationSelects({ allDistricts = false, onChange }) {
  const regions = await loadRegions();
  const regionSelect = document.getElementById("place-region");
  const districtSelect = document.getElementById("place-district");

  regionSelect.insertAdjacentHTML(
    "beforeend",
    regions.map((region) => `<option value="${escapeHtml(region.name)}">${escapeHtml(region.name)}</option>`).join("")
  );

  const fillDistricts = (keepDistrict = "") => {
    const region = regions.find((item) => item.name === regionSelect.value);
    const placeholder = region ? (allDistricts ? "Barcha tumanlar" : "Tumanni tanlang") : "Avval viloyatni tanlang";
    districtSelect.innerHTML =
      `<option value="">${placeholder}</option>` +
      (region ? region.districts.map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join("") : "");
    districtSelect.disabled = !region;
    if (keepDistrict && region?.districts.includes(keepDistrict)) districtSelect.value = keepDistrict;
  };

  const notify = () => {
    lastSchoolPlace = { region: regionSelect.value, district: districtSelect.value };
    onChange?.(lastSchoolPlace);
  };

  regionSelect.addEventListener("change", () => {
    fillDistricts();
    notify();
  });
  districtSelect.addEventListener("change", notify);

  // Oldingi tanlovni tiklash
  if (lastSchoolPlace.region && regions.some((region) => region.name === lastSchoolPlace.region)) {
    regionSelect.value = lastSchoolPlace.region;
    fillDistricts(lastSchoolPlace.district);
  }
  notify();
}

function schoolListHtml(schools, emptyText) {
  if (!schools.length) return `<p class="subtitle" style="margin:8px 0 0">${emptyText}</p>`;
  return `
    <div class="panel pick-list">
      ${schools
        .map(
          (school, index) => `
            <button type="button" class="pick-item" data-school-index="${index}">
              <span class="pick-title">🏫 ${escapeHtml(school.name)}
                <small>📍 ${escapeHtml(school.district)} · ${school.teachers} ta o'qituvchi${school.is_mine ? " · 👑 sizning maktabingiz" : ""}</small>
              </span>
              <span>${school.is_mine ? "›" : "🔒"}</span>
            </button>`
        )
        .join("")}
    </div>`;
}

async function fetchSchools(regionName, district = "") {
  const query = `region=${encodeURIComponent(regionName)}${district ? `&district=${encodeURIComponent(district)}` : ""}`;
  return api(`/api/schools?${query}`);
}

/*
  Tanlangan joydagi maktablar ro'yxatini yangilaydi. Tez-tez almashtirilsa,
  faqat oxirgi so'rov natijasi ko'rsatiladi (eski javob ustiga yozib qo'ymasin).
*/
let schoolListRequest = 0;

async function showSchoolsFor(place, container, { emptyText, onPick }) {
  const requestId = ++schoolListRequest;
  if (!place.region) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = `<p class="subtitle" style="margin:8px 0 0">⏳ Yuklanmoqda...</p>`;
  let schools;
  try {
    schools = await fetchSchools(place.region, place.district);
  } catch (error) {
    if (requestId === schoolListRequest) container.innerHTML = `<p class="subtitle">${escapeHtml(error.message)}</p>`;
    return;
  }
  if (requestId !== schoolListRequest || !container.isConnected) return;

  container.innerHTML = schoolListHtml(schools, emptyText);
  container.querySelectorAll("[data-school-index]").forEach((item) => {
    item.addEventListener("click", guarded(() => onPick(schools[Number(item.dataset.schoolIndex)])));
  });
}

// 🔑 Maktabga kirish: viloyat/tuman tanlanadi, pastda maktablar ro'yxati
async function renderJoinBrowser() {
  showPage(`
    ${schoolPageHeader("Maktabga kirish", "back-start")}
    ${locationSelectsHtml({ allDistricts: true })}
    <p class="section-label" style="margin-top:18px">MAKTABLAR</p>
    <div id="school-results"><p class="subtitle" style="margin:8px 0 0">Viloyatni tanlang — u yerdagi maktablar shu yerda chiqadi.</p></div>
    <button type="button" class="btn btn-ghost" id="code-only" style="margin-top:18px">⌨️ Kodim bor — to'g'ridan-to'g'ri kirish</button>
  `);

  document.querySelector('[data-action="back-start"]').addEventListener("click", renderPrivateSchoolStart);
  document.getElementById("code-only").addEventListener("click", guarded(() => renderJoinSchool({ onBack: renderJoinBrowser })));

  const results = document.getElementById("school-results");
  await wireLocationSelects({
    allDistricts: true,
    onChange: (place) =>
      place.region
        ? showSchoolsFor(place, results, {
            emptyText: "Bu yerda hali xususiy maktab ro'yxatdan o'tmagan.",
            onPick: (school) => renderJoinSchool({ school, onBack: renderJoinBrowser }),
          })
        : (results.innerHTML = `<p class="subtitle" style="margin:8px 0 0">Viloyatni tanlang — u yerdagi maktablar shu yerda chiqadi.</p>`),
  });
}

/*
  O'qituvchi fanlari — ixcham ochiluvchi ro'yxat(lar).

  Fan majburiy — har bir o'qituvchi kamida bitta fanni tanlaydi. Bir nechta fandan dars beradigan
  "＋ Yana fan qo'shish" bilan yana bitta ro'yxat qo'shadi. Ro'yxatda yo'q fan
  uchun "Boshqa fan..." tanlansa, nomini yozadigan maydon chiqadi.
*/
const NOT_SUBJECT_TEACHER = "__not_teacher__";
const OTHER_SUBJECT = "__other__";

function subjectSelectRowHtml(value = "", isFirst = false) {
  const known = schoolSubjectOptions();
  const isCustom = value && value !== NOT_SUBJECT_TEACHER && !known.includes(value);

  return `
    <div class="subject-row">
      <div class="subject-row-main">
        <select class="subject-select">
          <option value="">Fanni tanlang</option>
          ${known.map((subject) => `<option value="${escapeHtml(subject)}" ${subject === value ? "selected" : ""}>${escapeHtml(subject)}</option>`).join("")}
          <option value="${OTHER_SUBJECT}" ${isCustom ? "selected" : ""}>Boshqa fan...</option>
        </select>
        ${isFirst ? "" : `<button type="button" class="subject-remove" aria-label="Olib tashlash">✕</button>`}
      </div>
      <input class="input subject-custom" placeholder="Fan nomini yozing" value="${isCustom ? escapeHtml(value) : ""}"
             ${isCustom ? "" : "hidden"} />
    </div>`;
}

// `selected` — profildagi fanlar; bo'sh ro'yxat va `notTeacher` — "fan o'qituvchisi emas"
function subjectChipsHtml(selected = []) {
  // Fan majburiy: har bir o'qituvchi kamida bitta fanni tanlaydi
  const values = selected.length ? selected : [""];
  const notTeacher = false;
  return `
    <div id="subject-selects">
      ${values.map((value, index) => subjectSelectRowHtml(value, index === 0)).join("")}
    </div>
    <button type="button" class="btn btn-ghost" id="add-subject-button" style="margin:6px 0 0"
            ${notTeacher ? "hidden" : ""}>＋ Yana fan qo'shish</button>`;
}

function wireSubjectChips() {
  const container = document.getElementById("subject-selects");
  const addButton = document.getElementById("add-subject-button");

  const refresh = () => {
    const rows = [...container.querySelectorAll(".subject-row")];
    const notTeacher = rows[0]?.querySelector(".subject-select").value === NOT_SUBJECT_TEACHER;
    // "Fan o'qituvchisi emasman" tanlansa — boshqa fanlar kerak emas
    rows.slice(1).forEach((row) => (row.hidden = notTeacher));
    addButton.hidden = notTeacher;
    rows.forEach((row) => {
      const custom = row.querySelector(".subject-custom");
      custom.hidden = row.querySelector(".subject-select").value !== OTHER_SUBJECT;
    });
  };

  container.addEventListener("change", (event) => {
    if (event.target.classList.contains("subject-select")) {
      refresh();
      if (event.target.value === OTHER_SUBJECT) event.target.closest(".subject-row").querySelector(".subject-custom").focus();
    }
  });
  container.addEventListener("click", (event) => {
    const remove = event.target.closest(".subject-remove");
    if (remove) {
      remove.closest(".subject-row").remove();
      markUnsaved();
    }
  });
  addButton.addEventListener("click", () => {
    container.insertAdjacentHTML("beforeend", subjectSelectRowHtml());
    markUnsaved();
  });

  refresh();
}

function isNotSubjectTeacher() {
  return document.querySelector("#subject-selects .subject-select")?.value === NOT_SUBJECT_TEACHER;
}

function readSelectedSubjects() {
  if (isNotSubjectTeacher()) return [];

  const subjects = [];
  document.querySelectorAll("#subject-selects .subject-row").forEach((row) => {
    const choice = row.querySelector(".subject-select").value;
    const value = choice === OTHER_SUBJECT ? row.querySelector(".subject-custom").value.trim().replace(/\s+/g, " ") : choice;
    if (value && !subjects.some((item) => item.toLowerCase() === value.toLowerCase())) subjects.push(value);
  });
  return subjects;
}

// Saqlashdan oldin: kamida bitta fan tanlangan bo'lsin (majburiy)
function readSubjectsOrThrow() {
  const subjects = readSelectedSubjects();
  if (!subjects.length) {
    throw new Error("Qaysi fandan dars berishingizni tanlang — bu majburiy");
  }
  return subjects;
}

async function profileNameFieldsHtml() {
  const me = await api("/api/me");
  const [first = "", ...rest] = String(me.full_name || "").split(" ");
  return `
    <label class="field-label" for="profile-first-name">Ismingiz</label>
    <input class="input" id="profile-first-name" placeholder="Ism" value="${escapeHtml(first)}" />
    <label class="field-label" for="profile-last-name">Familiyangiz</label>
    <input class="input" id="profile-last-name" placeholder="Familiya" value="${escapeHtml(rest.join(" "))}" />`;
}

/*
  Telefon raqam — har bir xodim uchun majburiy (rahbariyat ko'radi).
  Telegram'dan bitta bosishda olish ham mumkin.
*/
function phoneFieldHtml(value = "", { birthDate = "", passport = "" } = {}) {
  return `
    <label class="field-label" for="profile-phone">Telefon raqamingiz <span class="required-mark">*</span></label>
    <div class="phone-field">
      <input class="input" id="profile-phone" type="tel" inputmode="tel" placeholder="+998 90 123 45 67"
             value="${escapeHtml(value ? formatPhone(value) : "+998 ")}" />
      ${telegram?.requestContact ? `<button type="button" class="share-pill" id="phone-from-telegram">📱 Telegram'dan</button>` : ""}
    </div>

    <label class="field-label" for="profile-birth-date">Tug'ilgan sanangiz <span class="required-mark">*</span></label>
    <input class="input" id="profile-birth-date" type="date" min="1930-01-01" max="${new Date().toISOString().slice(0, 10)}"
           value="${escapeHtml(birthDate || "")}" />

    <label class="field-label" for="profile-passport">Pasport seriya va raqami <span class="subtitle">(ixtiyoriy, lekin kerakli)</span></label>
    <input class="input" id="profile-passport" placeholder="AA1234567" maxlength="11"
           style="text-transform:uppercase;letter-spacing:1px" value="${escapeHtml(passport || "")}" />
    <p class="subtitle" style="margin:4px 0 0">🔒 Telefon, tug'ilgan sana va pasportni faqat maktab rahbariyati ko'radi.</p>`;
}

// Telefon, tug'ilgan sana va pasport — formadan o'qib, tekshiradi
function readStaffFieldsOrThrow() {
  const phone = readPhoneOrThrow();
  const birthDate = document.getElementById("profile-birth-date")?.value || "";
  if (!birthDate) throw new Error("Tug'ilgan sanangizni kiriting");
  const passport = (document.getElementById("profile-passport")?.value || "").replace(/\s/g, "").toUpperCase();
  if (passport && !/^[A-Z]{2}\d{7}$/.test(passport)) throw new Error("Pasport seriya va raqami noto'g'ri (masalan AA1234567)");
  return { phone, birth_date: birthDate, passport };
}

// "+998 " o'chirib yuborilsa — qaytadan qo'yiladi
// Kursor oxirga qo'yiladi — raqam "+998" dan keyin yoziladi
document.addEventListener("focusin", (event) => {
  const input = event.target;
  if (input.id !== "profile-phone") return;
  if (!input.value.trim()) input.value = "+998 ";
  setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
});

function formatPhone(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  if (digits.length !== 12) return phone || "";
  return `+${digits.slice(0, 3)} ${digits.slice(3, 5)} ${digits.slice(5, 8)} ${digits.slice(8, 10)} ${digits.slice(10)}`;
}

function readPhoneOrThrow() {
  let digits = (document.getElementById("profile-phone")?.value || "").replace(/\D/g, "");
  if (digits.length === 9) digits = `998${digits}`;
  if (digits.length !== 12 || !digits.startsWith("998")) {
    throw new Error("Telefon raqamingizni to'g'ri kiriting (masalan +998 90 123 45 67)");
  }
  return `+${digits}`;
}

// "📱 Telegram'dan" — foydalanuvchi ruxsat bersa, raqam o'zi yoziladi
document.addEventListener("click", (event) => {
  if (!event.target.closest("#phone-from-telegram")) return;
  telegram.requestContact((shared, response) => {
    const phone = response?.responseUnsafe?.contact?.phone_number;
    const input = document.getElementById("profile-phone");
    if (shared && phone && input) {
      input.value = formatPhone(phone.startsWith("+") ? phone : `+${phone}`);
      markUnsaved();
    } else if (!shared) {
      showToast("Raqamni qo'lda yozing");
    }
  });
});

async function saveProfileName() {
  const firstName = document.getElementById("profile-first-name").value.trim();
  const lastName = document.getElementById("profile-last-name").value.trim();
  if (firstName.length < 2 || lastName.length < 2) throw new Error("Ism va familiyangizni to'liq kiriting");
  const result = await api("/api/me/name", { method: "POST", body: { first_name: firstName, last_name: lastName } });
  if (homeSnapshot) homeSnapshot.full_name = result.full_name;
}

function protectSchoolForm() {
  const page = appElement.querySelector(".page");
  page.addEventListener("input", markUnsaved);
  page.addEventListener("change", markUnsaved);
}

// 📝 Maktabni ro'yxatdan o'tkazish — hammasi bitta formada
async function renderCreateSchool() {
  const nameFields = await profileNameFieldsHtml();

  showPage(`
    ${schoolPageHeader("Maktabni ro'yxatdan o'tkazish", "back-start")}
    ${locationSelectsHtml()}
    <div id="existing-schools"></div>

    <label class="field-label" for="school-name-input">Maktab nomi</label>
    <input class="input" id="school-name-input" placeholder="Masalan: Kelajak xususiy maktabi" />

    <p class="section-label" style="margin-top:18px">MAKTAB ADMINI</p>
    <p class="subtitle" style="margin:0 0 6px">
      Maktabni ochgan kishi — maktab admini. Admin sinflar yaratadi, ularga kurator tayinlaydi
      va o'qituvchilarni boshqaradi.
    </p>
    ${nameFields}
    ${phoneFieldHtml()}

    <button class="btn" id="create-school-button" style="margin-top:18px">🏫 Maktabni ochish</button>
  `);

  protectSchoolForm();
  document.querySelector('[data-action="back-start"]').addEventListener(
    "click",
    guarded(async () => {
      if (await confirmLeave()) renderPrivateSchoolStart();
    })
  );

  // Viloyat tanlanishi bilan shu yerda ochilgan maktablar ko'rinadi —
  // o'qituvchining maktabi bor bo'lsa, yangisini ochmay, unga qo'shiladi.
  const existing = document.getElementById("existing-schools");
  await wireLocationSelects({
    onChange: async (place) => {
      if (!place.region) {
        existing.innerHTML = "";
        return;
      }
      existing.innerHTML = `
        <p class="section-label" style="margin-top:16px">${place.district ? "BU TUMANDA" : "BU VILOYATDA"} OCHILGAN MAKTABLAR</p>
        <div id="existing-list"></div>
        <p class="subtitle" style="margin:6px 0 0">Maktabingiz ro'yxatda bo'lsa — yangisini ochmang, uni bosib qo'shiling.</p>`;
      await showSchoolsFor(place, document.getElementById("existing-list"), {
        emptyText: "Hali maktab ochilmagan — birinchi bo'lib oching!",
        onPick: async (school) => {
          if (await confirmLeave()) await renderJoinSchool({ school, onBack: renderCreateSchool });
        },
      });
    },
  });

  document.getElementById("create-school-button").addEventListener(
    "click",
    guarded(async () => {
      const region = document.getElementById("place-region").value;
      const district = document.getElementById("place-district").value;
      if (!region) throw new Error("Viloyatni tanlang");
      if (!district) throw new Error("Tumanni tanlang");
      const name = document.getElementById("school-name-input").value.trim();
      if (name.length < 2) throw new Error("Maktab nomini kiriting");
      const staff = readStaffFieldsOrThrow();
      await saveProfileName();
      const { school } = await api("/api/schools", {
        method: "POST",
        body: { region, district, name, subjects: [], ...staff },
      });

      showToast(`✅ Maktab ochildi! Kodi: ${school.code}`);
      renderMySchool(school);
    })
  );
}

// O'z maktabi (admin) — kod kerak emas, bir bosishda kiradi
function renderOwnSchoolReturn(school, onBack) {
  showPage(`
    ${schoolPageHeader("Maktabimga kirish", "back-join")}
    <div class="card" style="cursor:default">
      <div class="card-icon">🏫</div>
      <div class="card-body">
        <h3 style="margin:0">${escapeHtml(school.name)}</h3>
        <p class="subtitle" style="margin:4px 0 0">📍 ${escapeHtml(school.region)}, ${escapeHtml(school.district)}</p>
        <p style="margin:8px 0 0">👑 Bu sizning maktabingiz — siz uning adminisiz. Kod kerak emas.</p>
      </div>
    </div>
    <button class="btn" id="return-own-school" style="margin-top:16px">🏫 Maktabimga kirish</button>
  `);

  document.querySelector('[data-action="back-join"]').addEventListener("click", guarded(onBack));
  document.getElementById("return-own-school").addEventListener(
    "click",
    guarded(async () => {
      const { school: joined } = await api(`/api/schools/${school.id}/return`, { method: "POST", body: {} });
      showToast(`✅ «${joined.name}» — xush kelibsiz!`);
      renderMySchool(joined);
    })
  );
}

async function renderJoinSchool({ school: target = null, onBack = renderPrivateSchoolStart, prefillCode = "" } = {}) {
  if (target?.is_mine) {
    renderOwnSchoolReturn(target, onBack);
    return;
  }
  const nameFields = await profileNameFieldsHtml();
  const founder = target?.founder;

  showPage(`
    ${schoolPageHeader("Maktabga kirish", "back-join")}
    ${
      target
        ? `<div class="card" style="cursor:default">
             <div class="card-body" style="width:100%">
               <h3 style="margin:0">🏫 ${escapeHtml(target.name)}</h3>
               <p class="subtitle" style="margin:4px 0 0">📍 ${escapeHtml(target.region)}, ${escapeHtml(target.district)}</p>
               <p style="margin:10px 0 0">👤 Admin: <b>${escapeHtml(founder?.name || "—")}</b>
                 ${founder?.username ? `<span class="subtitle">@${escapeHtml(founder.username)}</span>` : ""}</p>
               <p class="subtitle" style="margin:8px 0 0">
                 Maktab kodini admindan oling. "Kod so'rash"ni bossangiz, bot adminga xabar yuboradi
                 va sizga ham nima qilishni tushuntiradi.
               </p>
               <div class="row-between" style="gap:8px;margin-top:10px">
                 ${
                   founder?.username
                     ? `<button type="button" class="btn btn-ghost" id="write-founder" style="margin:0">✉️ Adminga yozish</button>`
                     : ""
                 }
                 <button type="button" class="btn btn-ghost" id="request-code" style="margin:0">🔔 Kod so'rash</button>
               </div>
             </div>
           </div>`
        : `<p class="subtitle">Maktab kodini maktabingiz admini yoki o'qituvchisidan oling.</p>`
    }

    <label class="field-label" for="school-code-input">Maktab kodi</label>
    <input class="input" id="school-code-input" placeholder="Maktab kodi (8 belgi)" maxlength="12" value="${escapeHtml(prefillCode)}"
           style="text-transform:uppercase;letter-spacing:3px;text-align:center;font-size:18px" />

    <p class="section-label" style="margin-top:18px">SIZNING PROFILINGIZ</p>
    ${nameFields}
    ${phoneFieldHtml()}

    <label class="field-label">Siz sinf kuratorimisiz (sinf rahbari)?</label>
    <div class="choice-toggle" id="curator-toggle">
      <button type="button" data-curator="no" class="active">Yo'q</button>
      <button type="button" data-curator="yes">Ha, kuratorman</button>
    </div>
    <div id="curator-class-box" hidden>
      <label class="field-label" for="curator-class-select">Qaysi sinfning kuratorisiz? <span class="required-mark">*</span></label>
      <div id="curator-class-holder"><p class="subtitle" style="margin:4px 0 0">Avval maktab kodini kiriting.</p></div>
    </div>

    <label class="field-label" id="subject-label">Qaysi fanlardan dars berasiz? <span class="required-mark">*</span></label>
    ${subjectChipsHtml()}

    <button class="btn" id="join-school-button" style="margin-top:18px">🔑 Maktabga qo'shilish</button>
  `);

  protectSchoolForm();
  wireSubjectChips();

  // "Kuratormisiz?" — Ha: o'z sinfini tanlaydi (fan ixtiyoriy); Yo'q: fan majburiy
  let isCurator = false;
  let loadedFor = "";
  const codeInput = document.getElementById("school-code-input");
  const classBox = document.getElementById("curator-class-box");
  const classHolder = document.getElementById("curator-class-holder");
  const subjectLabel = document.getElementById("subject-label");

  const loadOpenClasses = async () => {
    const code = codeInput.value.trim().toUpperCase();
    if (code.length < 6) {
      classHolder.innerHTML = `<p class="subtitle" style="margin:4px 0 0">Avval maktab kodini kiriting.</p>`;
      loadedFor = "";
      return;
    }
    if (code === loadedFor) return;
    loadedFor = code;
    classHolder.innerHTML = `<p class="subtitle" style="margin:4px 0 0">⏳ Sinflar yuklanmoqda...</p>`;
    try {
      const result = await api("/api/schools/lookup", {
        method: "POST",
        body: { code, school_id: target ? target.id : null },
      });
      classHolder.innerHTML = result.open_classes.length
        ? `<select id="curator-class-select">
             <option value="">Sinfni tanlang</option>
             ${result.open_classes.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}
           </select>`
        : `<p class="subtitle" style="margin:4px 0 0">«${escapeHtml(result.school.name)}» maktabida kuratorsiz sinf yo'q — admin sinfingizni yaratgach, sizni kurator qilib tayinlaydi.</p>`;
    } catch (error) {
      loadedFor = "";
      classHolder.innerHTML = `<p class="subtitle" style="margin:4px 0 0;color:#d93636">${escapeHtml(error.message)}</p>`;
    }
  };

  document.getElementById("curator-toggle").addEventListener("click", (event) => {
    const button = event.target.closest("[data-curator]");
    if (!button) return;
    isCurator = button.dataset.curator === "yes";
    document.querySelectorAll("#curator-toggle [data-curator]").forEach((item) => item.classList.toggle("active", item === button));
    classBox.hidden = !isCurator;
    subjectLabel.innerHTML = isCurator
      ? "Qaysi fanlardan dars berasiz? (ixtiyoriy)"
      : 'Qaysi fanlardan dars berasiz? <span class="required-mark">*</span>';
    if (isCurator) loadOpenClasses();
  });
  codeInput.addEventListener("change", () => {
    if (isCurator) loadOpenClasses();
  });
  document.querySelector('[data-action="back-join"]').addEventListener(
    "click",
    guarded(async () => {
      if (await confirmLeave()) await onBack();
    })
  );

  document.getElementById("write-founder")?.addEventListener("click", () => openExternalLink(founder.link));

  document.getElementById("request-code")?.addEventListener(
    "click",
    guarded(async () => {
      // So'rovda ism ko'rinadi — avval profil ismini saqlab olamiz
      await saveProfileName();
      const result = await api(`/api/schools/${target.id}/request-code`, { method: "POST", body: {} });
      if (result.delivered) {
        showToast("📨 So'rov adminga yuborildi. Bot sizga ham xabar yozdi.");
      } else {
        showToast("Adminga xabar yetmadi — unga to'g'ridan-to'g'ri yozing");
      }
    })
  );

  document.getElementById("join-school-button").addEventListener(
    "click",
    guarded(async () => {
      const code = document.getElementById("school-code-input").value.trim().toUpperCase();
      if (code.length < 4) throw new Error("Maktab kodini kiriting");
      const subjects = isCurator ? readSelectedSubjects() : readSubjectsOrThrow();
      const classSelect = document.getElementById("curator-class-select");
      const curatorClassId = isCurator && classSelect ? Number(classSelect.value) || null : null;
      if (isCurator && classSelect && !curatorClassId) throw new Error("Qaysi sinfning kuratori ekaningizni tanlang");
      const staff = readStaffFieldsOrThrow();

      const confirmed = await askConfirm(
        "Maktabga qo'shilasizmi? Siz shu maktabda qolasiz — chiqish uchun ham maktab kodi kerak bo'ladi."
      );
      if (!confirmed) return;

      await saveProfileName();
      const { school } = await api("/api/schools/join", {
        method: "POST",
        body: { code, subjects, ...staff, school_id: target ? target.id : null, is_curator: isCurator, curator_class_id: curatorClassId },
      });
      showToast(`✅ "${school.name}" maktabiga qo'shildingiz`);
      renderMySchool(school);
    })
  );
}

function subjectsText(subjects) {
  return subjects.length ? subjects.map(escapeHtml).join(", ") : "⚠️ Fan kiritilmagan";
}

const POSITION_BADGES = {
  director: `<span class="badge badge-blue">🎓 Direktor</span>`,
  deputy: `<span class="badge badge-blue">🎖 Zam direktor</span>`,
};

function roleBadge(role, position = null) {
  if (role === "owner") return `<span class="badge badge-purple">👑 Admin</span>`;
  return POSITION_BADGES[position] || "";
}

// Maktabni ochgan admin — lavozim tayinlaydi, fani so'ralmaydi
function isOwner(school) {
  return school?.my_role === "owner";
}

// Rahbariyat (admin, direktor, zam direktor) — sinf yaratadi, o'qituvchi qo'shadi
function isLeader(school) {
  return Boolean(school?.is_leader);
}

function avatarLetter(name) {
  return escapeHtml((name || "?").trim().charAt(0).toUpperCase());
}

// Xodim rasmi (Telegram profil rasmi) yoki bosh harfi
function memberAvatarHtml(member, size = 38) {
  const style = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.4)}px`;
  if (member?.photo_url) {
    return `<div class="member-avatar" style="${style}"><img src="${escapeHtml(member.photo_url)}" alt="" loading="lazy"
              onerror="this.replaceWith(document.createTextNode('${avatarLetter(member.name)}'))" /></div>`;
  }
  return `<div class="member-avatar" style="${style}">${avatarLetter(member?.name)}</div>`;
}

/* ---------- Maktab bo'limi: pastki menyu (Maktab · Profil) ---------- */

/*
  O'qituvchilar va sinflar — Maktab ekranidagi bo'limlar orqali, oddiy
  (menyusiz) ekranlarda ochiladi.
*/
const SCHOOL_TABS = [
  { id: "school", icon: "🏫", label: "Maktab" },
  { id: "profile", icon: "👤", label: "Profil" },
];

// Oxirgi yuklangan maktab — tablar orasida darhol almashish uchun
let currentSchool = null;

function showSchoolNav(activeTab) {
  // O'qituvchi bo'limidagi menyu kabi: sahifadan tashqarida turadi va qayta chizilmaydi
  let host = document.getElementById("school-nav-host");

  if (!host) {
    host = document.createElement("div");
    host.id = "school-nav-host";
    host.className = "bottom-nav";
    host.innerHTML = SCHOOL_TABS.map(
      (tab) => `
        <button class="nav-item" type="button" data-school-nav="${tab.id}">
          <span class="nav-icon">${tab.icon}</span>
          <span>${tab.label}</span>
        </button>`
    ).join("");
    document.body.appendChild(host);

    host.addEventListener("click", (event) => {
      const button = event.target.closest("[data-school-nav]");
      if (!button || button.classList.contains("active") || !currentSchool) return;
      openSchoolTab(button.dataset.schoolNav);
    });
  }

  host.hidden = false;
  host.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.schoolNav === activeTab);
  });
}

function openSchoolTab(tab) {
  if (tab === "teachers") renderSchoolTeachers(currentSchool);
  else if (tab === "classes-mine") renderSchoolClasses(currentSchool, "mine");
  else if (tab === "classes-all") renderSchoolClasses(currentSchool, "all");
  else if (tab === "profile") renderSchoolProfile(currentSchool);
  else renderMySchool(currentSchool);
}

/*
  Tab darhol eslangan ma'lumot bilan chiziladi, keyin maktab fonda qayta
  so'raladi (yangi o'qituvchi qo'shilgan bo'lishi mumkin). O'zgargan bo'lsa
  va foydalanuvchi hali shu tabda bo'lsa — jimgina yangilanadi.
*/
async function refreshSchoolInBackground(tab) {
  try {
    const { school } = await api("/api/schools/me");
    if (!school) return;
    const changed = JSON.stringify(school) !== JSON.stringify(currentSchool);
    currentSchool = school;
    const stillHere = document.querySelector(`[data-school-tab="${tab}"]`);
    if (changed && stillHere) openSchoolTab(tab);
  } catch (_) {
    /* fonda — xato bo'lsa eslangan ma'lumot qoladi */
  }
}

function schoolTabHeader(title) {
  return `
    <div class="page-header">
      <button class="back-button" type="button" data-action="back-folder" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(title)}</h2>
    </div>`;
}

function wireSchoolTab(tab) {
  showSchoolNav(tab);
  document.querySelector('[data-action="back-folder"]').addEventListener("click", renderSchoolFolder);
}

// 🏫 Maktab tabi: maktab haqida ma'lumot
function renderMySchool(school, { refresh = true } = {}) {
  currentSchool = school;
  schoolMembershipKnown = true;

  showPage(`
    <div data-school-tab="school">
      ${schoolTabHeader(school.name)}
      <div class="card" style="cursor:default">
        <div class="card-icon">🏫</div>
        <div class="card-body">
          <h3 style="margin:0">${escapeHtml(school.name)}</h3>
          <p style="margin:4px 0 0">📍 ${escapeHtml(school.region)}, ${escapeHtml(school.district)}</p>
          <p class="subtitle" style="margin:4px 0 0">👥 ${school.members.length} ta xodim</p>
        </div>
      </div>
      ${
        school.my_subjects.length || isOwner(school)
          ? ""
          : `<div class="card warning-card" data-action="fill-subjects">
               <div class="card-icon">⚠️</div>
               <div class="card-body">
                 <h3 style="margin:0">Fanni kiriting</h3>
                 <p style="margin:4px 0 0">Profilingizda qaysi fandan dars berishingiz ko'rsatilmagan — bu majburiy.</p>
               </div>
             </div>`
      }

      ${
        school.my_phone
          ? ""
          : `<div class="card warning-card" data-action="fill-phone">
               <div class="card-icon">📱</div>
               <div class="card-body">
                 <h3 style="margin:0">Telefon raqamingizni kiriting</h3>
                 <p style="margin:4px 0 0">Bu majburiy — rahbariyat siz bilan bog'lana olishi uchun.</p>
               </div>
             </div>`
      }

      <p class="section-label" style="margin-top:22px">BO'LIMLAR</p>
      <div class="school-tiles">
        ${schoolMenuItem("classes-mine", "⭐", "Mening sinflarim", '<i class="menu-count" data-count="mine">…</i>')}
        ${schoolMenuItem("classes-all", "📚", "Barcha sinflar", '<i class="menu-count" data-count="all">…</i>')}
        ${isLeader(school) ? schoolMenuItem("results", "📊", "Test natijalari", "Sinflar bo'yicha", "wide") : ""}
        ${schoolMenuItem("teachers", "👥", "Xodimlar", `${school.members.length} ta xodim`, "wide")}
      </div>

    </div>
  `);

  wireSchoolTab("school");
  document.querySelector('[data-action="fill-subjects"]')?.addEventListener("click", guarded(() => renderSchoolProfileEdit(school)));
  document.querySelector('[data-action="fill-phone"]')?.addEventListener("click", guarded(() => renderSchoolProfileEdit(school)));
  document.querySelectorAll("[data-school-menu]").forEach((item) => {
    item.addEventListener(
      "click",
      guarded(async () => {
        const target = item.dataset.schoolMenu;
        if (target === "create-class") renderCreateClass();
        else if (target === "results") await renderSchoolResults();
        else if (target === "invite-teacher") await inviteTeacher();
        else openSchoolTab(target);
      })
    );
  });
  fillClassCounts();
  if (refresh) refreshSchoolInBackground("school");
}

// Maktab ekranidagi alohida kartochka (yonma-yon turadi)
function schoolMenuItem(target, icon, title, note, size = "") {
  return `
    <button type="button" class="school-tile${size ? ` is-${size}` : ""}" data-school-menu="${target}">
      <span class="school-tile-icon">${icon}</span>
      <span class="school-tile-text"><b>${title}</b><small>${note}</small></span>
    </button>`;
}

// Maktab ekranidagi "Mening sinflarim" / "Barcha sinflar" soni
async function fillClassCounts() {
  const show = (classes) => {
    const mine = classes.filter((item) => item.is_curator).length;
    const mineNode = document.querySelector('[data-count="mine"]');
    const allNode = document.querySelector('[data-count="all"]');
    if (mineNode) mineNode.textContent = mine ? `${mine} ta sinf` : "Hali yo'q";
    if (allNode) allNode.textContent = `${classes.length} ta sinf`;
  };
  if (myClassesCache) show(myClassesCache);
  try {
    myClassesCache = await api("/api/classes");
    show(myClassesCache);
  } catch (_) {
    /* soni ko'rinmasa ham bo'limlar ishlaydi */
  }
}

// Menyusiz ekranlar (o'qituvchilar, sinflar) sarlavhasi: ← Maktab ekraniga
function schoolSubHeader(title) {
  return `
    <div class="page-header">
      <button class="back-button" type="button" data-action="back-school-home" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(title)}</h2>
    </div>`;
}

function wireSchoolSubPage() {
  document.querySelector('[data-action="back-school-home"]').addEventListener("click", () => renderMySchool(currentSchool));
}

// 👥 O'qituvchilar tabi — hammasi bitta ekranda
function renderSchoolTeachers(school) {
  currentSchool = school;

  const canAssign = isOwner(school);
  const canManage = canAssign || Boolean(school.can_remove_members);
  // Oddiy o'qituvchi boshqa xodimlarning ma'lumotlarini ko'rmaydi — faqat ism va lavozim
  const leader = Boolean(school.can_see_phones);
  const memberRows = school.members
    .map(
      (member) => `
        <div class="member-line${leader || member.is_me ? " is-tappable" : ""}" data-member-id="${member.user_id}">
          ${memberAvatarHtml(member)}
          <div class="member-info">
            <b>${escapeHtml(member.name)}${member.is_me ? " (siz)" : ""}</b>
            ${
              member.role === "owner"
                ? `<small>Maktab admini</small>`
                : leader || member.is_me
                  ? `<small>${subjectsText(member.subjects)}</small>`
                  : ""
            }
            ${
              school.can_see_phones
                ? member.phone
                  ? `<a class="member-phone" href="tel:${escapeHtml(member.phone)}">📞 ${escapeHtml(formatPhone(member.phone))}</a>`
                  : `<small class="member-phone missing">📵 Raqam kiritilmagan</small>`
                : ""
            }
          </div>
          ${roleBadge(member.role, member.position) || `<span class="badge badge-blue">O'qituvchi</span>`}
        </div>`
    )
    .join("");

  showPage(`
    <div data-school-tab="teachers">
      ${schoolSubHeader(`Xodimlar (${school.members.length})`)}
      <div class="member-cards">${memberRows}</div>
      ${
        canManage && school.members.length > 1
          ? `<p class="subtitle" style="margin:8px 4px 0">Xodimni bosing — ma'lumotlari va bog'lanish${
              canAssign ? ", lavozim berish" : ""
            }.</p>`
          : ""
      }
      ${
        isLeader(school)
          ? `<button type="button" class="fab" id="invite-teacher-button" aria-label="O'qituvchi qo'shish" title="O'qituvchi qo'shish">
               <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                 <circle cx="9" cy="8" r="4"/><path d="M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 4 1.5"/><path d="M19 14v6M16 17h6"/>
               </svg>
             </button>`
          : ""
      }
    </div>
  `);

  wireSchoolSubPage();
  document.getElementById("invite-teacher-button")?.addEventListener("click", guarded(inviteTeacher));
  {
    document.querySelectorAll(".member-line.is-tappable").forEach((line) => {
      const member = school.members.find((item) => item.user_id === Number(line.dataset.memberId));
      line.addEventListener("click", (event) => {
        if (event.target.closest(".member-phone")) return; // raqam bosilsa — qo'ng'iroq
        renderStaffDetail(member);
      });
    });
  }
  refreshSchoolInBackground("teachers");
}

/*
  Xodim haqida to'liq ma'lumot: rasm, lavozim, fanlar, kurator sinfi va
  (rahbariyatga) telefon, tug'ilgan sana, pasport. Pastda — bog'lanish:
  qo'ng'iroq va Telegram. Admin/direktor shu yerdan lavozim beradi va chiqaradi.
*/
function renderStaffDetail(member) {
  const school = currentSchool;
  const canAssign = isOwner(school) && member.role !== "owner" && !member.is_me;
  const canRemove = (isOwner(school) || school.can_remove_members) && member.role !== "owner" && !member.is_me;
  const showPrivate = school.can_see_phones || member.is_me;
  const role = member.role === "owner" ? "Maktab admini" : { director: "Direktor", deputy: "Zam direktor" }[member.position] || "O'qituvchi";
  const telegramLink = member.username ? `https://t.me/${member.username}` : member.phone ? `https://t.me/${member.phone}` : "";
  const row = (label, value) =>
    `<div class="row-between"><span class="subtitle" style="margin:0">${label}</span><b style="text-align:right">${value}</b></div>`;
  const missing = `<span class="muted-value">Kiritilmagan</span>`;

  showPage(`
    ${schoolPageHeader("Xodim", "back-staff")}
    <div class="staff-hero">
      ${memberAvatarHtml(member, 84)}
      <h2 style="margin:10px 0 4px">${escapeHtml(member.name)}${member.is_me ? " (siz)" : ""}</h2>
      ${roleBadge(member.role, member.position) || `<span class="badge badge-blue">O'qituvchi</span>`}
    </div>

    <div class="panel profile-facts">
      ${row("Lavozim", role)}
      ${member.role === "owner" ? "" : row("Fanlar", subjectsText(member.subjects))}
      ${member.curator_of?.length ? row("Kurator", member.curator_of.map(escapeHtml).join(", ")) : ""}
      ${
        showPrivate
          ? `${row("Telefon", member.phone ? escapeHtml(formatPhone(member.phone)) : missing)}
             ${row("Tug'ilgan sana", member.birth_date ? formatDate(member.birth_date) : missing)}
             ${row("Pasport", member.passport ? escapeHtml(member.passport) : missing)}`
          : ""
      }
      ${row("Qo'shilgan", formatDate(member.joined_at))}
    </div>
    ${showPrivate ? "" : `<p class="subtitle" style="margin:8px 4px 0">🔒 Shaxsiy ma'lumotlarni faqat maktab rahbariyati ko'radi.</p>`}

    ${
      member.is_me
        ? ""
        : `<p class="section-label" style="margin-top:18px">BOG'LANISH</p>
           <div class="contact-actions">
             ${showPrivate && member.phone ? `<a class="btn" href="tel:${escapeHtml(member.phone)}">📞 Qo'ng'iroq</a>` : ""}
             ${telegramLink ? `<button class="btn btn-ghost" id="staff-telegram">✈️ Telegram'da yozish</button>` : ""}
           </div>
           ${!telegramLink && !(showPrivate && member.phone) ? `<p class="subtitle" style="margin:0">Bog'lanish ma'lumoti yo'q.</p>` : ""}`
    }

    ${canAssign || canRemove ? `<button class="btn btn-ghost" id="staff-manage" style="margin-top:16px">⚙️ ${canAssign ? "Lavozim va boshqaruv" : "Boshqaruv"}</button>` : ""}
  `);

  document.querySelector('[data-action="back-staff"]').addEventListener("click", () => renderSchoolTeachers(currentSchool));
  document.getElementById("staff-telegram")?.addEventListener("click", () => openExternalLink(telegramLink));
  document.getElementById("staff-manage")?.addEventListener("click", () => openPositionSheet(member, { canAssign }));
}

function formatDate(isoDate) {
  if (!isoDate) return "";
  const [year, month, day] = String(isoDate).slice(0, 10).split("-");
  return `${day}.${month}.${year}`;
}

/*
  Lavozim tayinlash (faqat admin): Direktor — bitta; Zam direktor — bir nechta.
  Sinf ochish, kurator tayinlash va sinfni o'chirish — faqat admin va direktor;
  zam direktor o'qituvchi qo'shadi va davomatlarni tahrirlaydi. O'quvchilarni
  admin, direktor va o'sha sinfning kuratori qo'shadi.
*/
function openPositionSheet(member, { canAssign = true } = {}) {
  const options = [
    { id: "", icon: "👤", title: "O'qituvchi", note: "Davomat oladi, kurator bo'lsa sinfini boshqaradi" },
    { id: "deputy", icon: "🎖", title: "Zam direktor", note: "O'qituvchi qo'shadi, davomatlarni tahrirlaydi" },
    { id: "director", icon: "🎓", title: "Direktor", note: "Admin ishlari: sinf ochadi, kurator tayinlaydi, sinfni o'chiradi" },
  ];
  const current = member.position || "";
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 4px">${escapeHtml(member.name)}</h3>
    ${
      canAssign
        ? `<p class="subtitle" style="margin:0 0 12px">Lavozimni tanlang</p>
           <div class="panel pick-list">
             ${options
               .map(
                 (option) => `
                   <button type="button" class="pick-item${option.id === current ? " is-selected" : ""}" data-position="${option.id}">
                     <span class="pick-title">${option.icon} ${option.title}<small>${option.note}</small></span>
                     <span>${option.id === current ? "✓" : ""}</span>
                   </button>`
               )
               .join("")}
           </div>`
        : ""
    }
    <button class="btn btn-ghost danger-text" id="sheet-remove-member" style="margin-top:14px">🚪 Maktabdan chiqarish</button>
    <button class="btn btn-ghost" data-sheet-close>Yopish</button>
  `);

  sheet.querySelector("#sheet-remove-member").addEventListener(
    "click",
    guarded(async () => {
      const sure = await askConfirm(
        `${member.name} maktabdan chiqarilsinmi? U endi maktab bo'limiga kira olmaydi. Olgan davomatlari tarixda saqlanib qoladi.`
      );
      if (!sure) return;
      const { school } = await api(`/api/schools/members/${member.user_id}`, { method: "DELETE" });
      currentSchool = school;
      myClassesCache = null;
      close();
      showToast(`🚪 ${member.name} maktabdan chiqarildi`);
      renderSchoolTeachers(school);
    })
  );

  sheet.querySelectorAll("[data-position]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(async () => {
        const position = button.dataset.position || null;
        if ((position || "") === current) return close();
        if (position === "director" && !(await askConfirm(`${member.name} direktor bo'ladimi? Avvalgi direktor (bo'lsa) oddiy o'qituvchiga qaytadi.`))) return;
        const { school } = await api(`/api/schools/members/${member.user_id}/position`, {
          method: "PUT",
          body: { position },
        });
        currentSchool = school;
        close();
        showToast(position ? `✅ ${member.name} — ${position === "director" ? "Direktor" : "Zam direktor"}` : `${member.name} — oddiy o'qituvchi`);
        renderSchoolTeachers(school);
      })
    );
  });
}

// 📚 Sinflarim tabi — o'qituvchi faqat o'zi dars beradigan sinf/guruhlari
// Sinfdan orqaga qaytilganda — qaysi ro'yxatdan kirilgan bo'lsa, o'shanga
let lastClassesScope = "mine";

function renderSchoolClasses(school, scope = "mine") {
  currentSchool = school;
  lastClassesScope = scope;
  const mine = scope === "mine";

  showPage(`
    <div data-school-tab="classes-${scope}">
      ${schoolSubHeader(mine ? "Mening sinflarim" : "Barcha sinflar")}
      ${
        !mine && school.can_create_classes
          ? `<button class="btn" id="create-class-button" style="margin:0 0 12px">➕ Sinf yaratish</button>`
          : ""
      }
      <p class="subtitle" style="margin:0">${
        mine
          ? "Kuratori siz bo'lgan sinflar — o'quvchilarni qo'shasiz va sinfni boshqarasiz."
          : "Maktabdagi barcha sinflar. ⭐ — siz kurator bo'lgan sinf."
      }</p>
      <div id="my-classes" style="margin-top:14px"><p class="subtitle" style="margin:6px 0 0">⏳ Yuklanmoqda...</p></div>
    </div>
  `);

  wireSchoolSubPage();
  document.getElementById("create-class-button")?.addEventListener("click", renderCreateClass);
  loadMyClassesInto(document.getElementById("my-classes"), scope);
}

// 👤 Profil tabi: ma'lumotlar, tahrirlash va maktabdan chiqish
/* ---------- O'qituvchini shaxsiy havola bilan qo'shish ---------- */

/*
  Admin "➕ O'qituvchi qo'shish"ni bosadi → Telegram'da chat (o'qituvchi)
  tanlanadi → unga bir martalik shaxsiy havola boradi. O'qituvchi havolani
  bosganda KODSIZ qo'shilish sahifasi ochiladi.
*/
async function inviteTeacher() {
  const invite = await api("/api/schools/invites", { method: "POST", body: {} });
  const text =
    `🏫 «${invite.school_name}» maktabiga o'qituvchi sifatida taklif qilaman.\n` +
    `Havolani bosing — kod kerak emas. Havola bir martalik, 24 soat amal qiladi.`;

  if (!invite.link) {
    await copyToClipboard(text, "📋 Taklif nusxalandi — o'qituvchiga yuboring");
    return;
  }
  openExternalLink(`https://t.me/share/url?url=${encodeURIComponent(invite.link)}&text=${encodeURIComponent(text)}`);
}

// Havoladan kelgan o'qituvchi — kodsiz qo'shilish sahifasi
async function renderInviteJoin(token) {
  const info = await api(`/api/schools/invites/${encodeURIComponent(token)}`);
  const nameFields = await profileNameFieldsHtml();
  const hasOpenClasses = info.open_classes.length > 0;

  showPage(`
    ${schoolPageHeader("Maktabga qo'shilish", "back-start")}
    <div class="card" style="cursor:default">
      <div class="card-icon">🏫</div>
      <div class="card-body">
        <h3 style="margin:0">${escapeHtml(info.school.name)}</h3>
        <p class="subtitle" style="margin:4px 0 0">📍 ${escapeHtml(info.school.region)}, ${escapeHtml(info.school.district)}</p>
        <p style="margin:8px 0 0">👤 ${escapeHtml(info.invited_by)} sizni taklif qildi — kod kerak emas.</p>
      </div>
    </div>

    <p class="section-label" style="margin-top:18px">SIZNING PROFILINGIZ</p>
    ${nameFields}
    ${phoneFieldHtml()}

    <label class="field-label">Siz sinf kuratorimisiz (sinf rahbari)?</label>
    <div class="choice-toggle" id="curator-toggle">
      <button type="button" data-curator="no" class="active">Yo'q</button>
      <button type="button" data-curator="yes">Ha, kuratorman</button>
    </div>
    <div id="curator-class-box" hidden>
      <label class="field-label" for="curator-class-select">Qaysi sinfning kuratorisiz? <span class="required-mark">*</span></label>
      ${
        hasOpenClasses
          ? `<select id="curator-class-select">
               <option value="">Sinfni tanlang</option>
               ${info.open_classes.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}
             </select>`
          : `<p class="subtitle" style="margin:4px 0 0">Kuratorsiz sinf hozircha yo'q — admin sinfingizni yaratgach, sizni tayinlaydi.</p>`
      }
    </div>

    <label class="field-label" id="subject-label">Qaysi fanlardan dars berasiz? <span class="required-mark">*</span></label>
    ${subjectChipsHtml()}

    <button class="btn" id="accept-invite-button" style="margin-top:18px">🏫 Maktabga qo'shilish</button>
  `);

  protectSchoolForm();
  wireSubjectChips();
  document.querySelector('[data-action="back-start"]').addEventListener(
    "click",
    guarded(async () => {
      if (await confirmLeave()) renderPrivateSchoolStart();
    })
  );

  let isCurator = false;
  document.getElementById("curator-toggle").addEventListener("click", (event) => {
    const button = event.target.closest("[data-curator]");
    if (!button) return;
    isCurator = button.dataset.curator === "yes";
    document.querySelectorAll("#curator-toggle [data-curator]").forEach((item) => item.classList.toggle("active", item === button));
    document.getElementById("curator-class-box").hidden = !isCurator;
    document.getElementById("subject-label").innerHTML = isCurator
      ? "Qaysi fanlardan dars berasiz? (ixtiyoriy)"
      : 'Qaysi fanlardan dars berasiz? <span class="required-mark">*</span>';
  });

  document.getElementById("accept-invite-button").addEventListener(
    "click",
    guarded(async () => {
      const subjects = isCurator ? readSelectedSubjects() : readSubjectsOrThrow();
      const classSelect = document.getElementById("curator-class-select");
      const curatorClassId = isCurator && classSelect ? Number(classSelect.value) || null : null;
      if (isCurator && classSelect && !curatorClassId) throw new Error("Qaysi sinfning kuratori ekaningizni tanlang");
      const staff = readStaffFieldsOrThrow();

      await saveProfileName();
      const { school } = await api(`/api/schools/invites/${encodeURIComponent(token)}/accept`, {
        method: "POST",
        body: { subjects, ...staff, is_curator: isCurator, curator_class_id: curatorClassId },
      });
      showToast(`✅ «${school.name}» maktabiga qo'shildingiz`);
      renderMySchool(school);
    })
  );
}

function renderSchoolProfile(school) {
  currentSchool = school;
  const me = school.members.find((member) => member.is_me);

  showPage(`
    <div data-school-tab="profile">
      ${schoolTabHeader("Profilim")}
      <div class="card" style="cursor:default">
        ${memberAvatarHtml(me, 52)}
        <div class="card-body" style="width:100%">
          <h3 style="margin:0">${escapeHtml(me ? me.name : "")}</h3>
          <p style="margin:6px 0 0">${roleBadge(school.my_role, school.my_position) || `<span class="badge badge-blue">O'qituvchi</span>`}</p>
        </div>
      </div>

      <div class="panel profile-facts">
        <div class="row-between"><span class="subtitle" style="margin:0">Maktab</span><b>${escapeHtml(school.name)}</b></div>
        <div class="row-between"><span class="subtitle" style="margin:0">${isOwner(school) ? "Lavozim" : "Fanlar"}</span><b>${isOwner(school) ? "Maktab admini" : subjectsText(school.my_subjects)}</b></div>
        <div class="row-between"><span class="subtitle" style="margin:0">Telefon</span><b>${school.my_phone ? escapeHtml(formatPhone(school.my_phone)) : "⚠️ Kiritilmagan"}</b></div>
        <div class="row-between"><span class="subtitle" style="margin:0">Tug'ilgan sana</span><b>${school.my_birth_date ? formatDate(school.my_birth_date) : "⚠️ Kiritilmagan"}</b></div>
        <div class="row-between"><span class="subtitle" style="margin:0">Pasport</span><b>${school.my_passport ? escapeHtml(school.my_passport) : "Kiritilmagan"}</b></div>
        <div class="row-between"><span class="subtitle" style="margin:0">Joylashuv</span><b>${escapeHtml(school.district)}</b></div>
      </div>

      ${
        school.code
          ? `<p class="section-label" style="margin-top:18px">MAKTAB KODI</p>
             <div class="card" style="cursor:default">
               <div class="card-body" style="width:100%">
                 ${copyCodeChip(school.code)}
                 <p class="subtitle" style="margin:8px 0 0">Kodni faqat siz ko'rasiz. Maktabdan chiqishda shu kod so'raladi. Bosing — nusxalanadi.</p>
               </div>
             </div>`
          : ""
      }

      <button class="btn" id="edit-school-profile" style="margin-top:20px">✏️ Profilni tahrirlash</button>
      ${
        isOwner(school)
          ? `<button class="btn btn-ghost" id="leave-school-button" style="margin-top:10px">🚪 Maktabdan chiqish</button>`
          : `<p class="subtitle" style="margin:12px 4px 0">Maktabdan chiqish kerak bo'lsa — maktab adminiga murojaat qiling.</p>`
      }
    </div>
  `);

  wireSchoolTab("profile");
  document.getElementById("edit-school-profile").addEventListener("click", guarded(() => renderSchoolProfileEdit(school)));
  document.getElementById("leave-school-button")?.addEventListener("click", () => renderLeaveSchool(school));
}

async function renderSchoolProfileEdit(school) {
  const nameFields = await profileNameFieldsHtml();

  showPage(`
    ${schoolPageHeader("Profilim", "back-school")}
    ${nameFields}
    ${phoneFieldHtml(school.my_phone || "", { birthDate: school.my_birth_date, passport: school.my_passport })}
    ${
      isOwner(school)
        ? ""
        : `<label class="field-label">Qaysi fanlardan dars berasiz? <span class="required-mark">*</span></label>
           ${subjectChipsHtml(school.my_subjects)}`
    }
    <button class="btn" id="save-school-profile" style="margin-top:18px">💾 Saqlash</button>
  `);

  protectSchoolForm();
  if (!isOwner(school)) wireSubjectChips();
  document.querySelector('[data-action="back-school"]').addEventListener(
    "click",
    guarded(async () => {
      if (await confirmLeave()) renderSchoolProfile(school);
    })
  );

  document.getElementById("save-school-profile").addEventListener(
    "click",
    guarded(async () => {
      const subjects = isOwner(school) ? [] : readSubjectsOrThrow();
      const staff = readStaffFieldsOrThrow();

      await saveProfileName();
      const { school: updated } = await api("/api/schools/me/profile", { method: "PUT", body: { subjects, ...staff } });
      showToast("✅ Profil saqlandi");
      renderSchoolProfile(updated);
    })
  );
}

function renderLeaveSchool(school) {
  showPage(`
    ${schoolPageHeader("Maktabdan chiqish", "back-school")}
    <div class="card" style="cursor:default">
      <div class="card-body">
        <p style="margin:0">
          "${escapeHtml(school.name)}" maktabidan chiqish uchun <b>maktab kodini</b> kiriting.
        </p>
      </div>
    </div>
    ${
      isOwner(school) && school.members.some((member) => !member.is_me)
        ? `<label class="field-label" for="successor-select">Adminlikni kimga topshirasiz? <span class="required-mark">*</span></label>
           <select id="successor-select">
             <option value="">O'qituvchini tanlang</option>
             ${school.members
               .filter((member) => !member.is_me)
               .map((member) => `<option value="${member.user_id}">${escapeHtml(member.name)} — ${subjectsText(member.subjects)}</option>`)
               .join("")}
           </select>
           <p class="subtitle" style="margin:4px 0 8px">Maktab adminsiz qolmasligi uchun o'rningizga yangi adminni tanlang.</p>`
        : ""
    }
    <input class="input" id="leave-code-input" placeholder="Maktab kodi" maxlength="12"
           style="text-transform:uppercase;letter-spacing:3px;text-align:center;font-size:18px" />
    <button class="btn" id="confirm-leave-button" style="margin-top:12px">🚪 Chiqish</button>
  `);

  document.querySelector('[data-action="back-school"]').addEventListener("click", () => renderSchoolProfile(school));
  document.getElementById("confirm-leave-button").addEventListener(
    "click",
    guarded(async () => {
      const code = document.getElementById("leave-code-input").value.trim().toUpperCase();
      if (code.length < 4) throw new Error("Maktab kodini kiriting");
      const successorSelect = document.getElementById("successor-select");
      const successorId = successorSelect ? Number(successorSelect.value) || null : null;
      if (successorSelect && !successorId) throw new Error("Adminlikni topshiradigan o'qituvchini tanlang");
      const successorName = successorSelect ? successorSelect.selectedOptions[0].textContent.split(" — ")[0] : "";
      const question = successorId
        ? `Adminlik ${successorName}ga o'tadi va siz "${school.name}" maktabidan chiqasiz. Davom etasizmi?`
        : `"${school.name}" maktabidan chiqasizmi?`;
      if (!(await askConfirm(question))) return;

      await api("/api/schools/leave", { method: "POST", body: { code, successor_user_id: successorId } });
      currentSchool = null;
      schoolMembershipKnown = true;
      showToast("Maktabdan chiqdingiz");
      renderPrivateSchoolStart();
    })
  );
}

/*
  Bosh sahifa ma'lumoti (ism, admin/o'qituvchi belgisi) eslab qolinadi.

  Ilgari "←" bilan bosh sahifaga har qaytishda server so'ralardi va sahifa
  1–2 soniya kechikib ochilardi — papkaga kirish esa darhol edi. Endi qaytish
  ham darhol: eslangan ma'lumot bilan chiziladi, yangilanish esa fonda
  so'raladi va faqat nimadir o'zgargan bo'lsagina sahifa yangilanadi.
*/
let homeSnapshot = null;

function applyHomeData(me) {
  isAdmin = Boolean(me.is_admin);
  isTeacher = Boolean(me.is_teacher);
}

async function loadHome() {
  if (homeSnapshot) {
    renderHome(homeSnapshot.full_name || null, true);
    refreshHomeInBackground();
    return;
  }

  renderSkeleton();
  try {
    const me = await api("/api/me");
    homeSnapshot = me;
    applyHomeData(me);
    renderHome(me.full_name || null, true);
  } catch (error) {
    isAdmin = false;
    isTeacher = false;
    renderHome(null, false);
  }
}

async function refreshHomeInBackground() {
  try {
    const me = await api("/api/me");
    const changed =
      me.full_name !== homeSnapshot.full_name ||
      Boolean(me.is_admin) !== Boolean(homeSnapshot.is_admin) ||
      Boolean(me.is_teacher) !== Boolean(homeSnapshot.is_teacher);

    homeSnapshot = me;
    applyHomeData(me);

    // Foydalanuvchi hali ham bosh sahifada bo'lsa va nimadir o'zgargan bo'lsa
    if (changed && document.querySelector('[data-action="school-folder"]')) {
      renderHome(me.full_name || null, true);
    }
  } catch (_) {
    /* fonda — xato bo'lsa eslangan ma'lumot qoladi */
  }
}

async function renderAdminDashboard() {
  const [stats, topStudents, withdrawalRequests, teachers, cardTopUps, promoCodes] = await Promise.all([
    api("/api/admin/stats"),
    api("/api/admin/top-students"),
    api("/api/admin/withdrawal-requests"),
    api("/api/admin/teachers"),
    api("/api/admin/card-topups"),
    api("/api/admin/promo-codes"),
  ]);

  const promoRows =
    promoCodes
      .map(
        (promo) => `
          <div class="card" style="cursor:default">
            <div class="card-body" style="width:100%">
              <div class="row-between">
                <b>🎁 ${escapeHtml(promo.hint)}</b>
                <b>+${formatNumber(promo.credits)} kredit</b>
              </div>
              <p class="subtitle" style="margin:4px 0 0">
                ${promo.used}${promo.max_uses ? ` / ${promo.max_uses}` : ""} marta ishlatildi • har kim ${promo.per_teacher_limit} martagacha
                ${promo.expires_on ? ` • ${formatDate(promo.expires_on)} gacha` : ""}
                • ${promo.active ? "✅ faol" : "⛔ to'xtatilgan"}
              </p>
              <button class="btn btn-ghost" data-promo-toggle="${promo.id}" style="padding:8px">${promo.active ? "⛔ To'xtatish" : "▶️ Qayta yoqish"}</button>
            </div>
          </div>`
      )
      .join("") || "<p>Hali promo kod yo'q</p>";

  // Karta orqali kredit so'rovlari. Asosan admin ularni Telegram'dagi chek
  // ostidagi tugma bilan tasdiqlaydi — bu yerdagisi zaxira yo'l.
  const cardTopUpRows =
    cardTopUps
      .map(
        (topUp) => `
          <div class="card" style="cursor:default" data-topup-id="${topUp.id}">
            <div class="card-body" style="width:100%">
              <div class="row-between">
                <span>${escapeHtml(topUp.teacher)}</span>
                <b>${formatNumber(topUp.amount)} so'm</b>
              </div>
              <p>${formatNumber(topUp.credits)} ta kredit • ${formatDateTime(topUp.created_at)}</p>
              ${topUp.has_receipt ? `<button class="btn btn-ghost" data-action="receipt" style="padding:8px">🧾 Chekni ko'rish</button>` : ""}
              <img data-receipt-image alt="" hidden
                   style="max-width:100%;border-radius:10px;margin:8px 0;display:block" />
              <div class="row-between" style="margin-top:8px;gap:8px">
                <button class="btn" data-action="approve" style="padding:8px">✅ Pul tushdi</button>
                <button class="btn btn-ghost" data-action="reject" style="padding:8px">❌ Rad etish</button>
              </div>
            </div>
          </div>`
      )
      .join("") || "<p>Tekshirilishi kerak bo'lgan to'lov yo'q</p>";

  // O'qituvchilar ro'yxati: eng ko'p to'lov qilgani birinchi
  const teacherRows =
    teachers
      .map(
        (teacher) => `
          <div class="card" style="cursor:default">
            <div class="card-body">
              <div class="row-between">
                <span>${escapeHtml(teacher.name)}</span>
                <b>${formatNumber(teacher.paid_total)} so'm</b>
              </div>
              <p class="subtitle" style="margin:4px 0 0">
                ${teacher.tests} ta test • ${formatNumber(teacher.credits)} kredit
                ${teacher.balance ? ` • balansi ${formatNumber(teacher.balance)} so'm` : ""}
              </p>
            </div>
          </div>`
      )
      .join("") || "<p>Hali o'qituvchi yo'q</p>";

  const topRows =
    topStudents
      .map(
        (row, index) => `
          <div class="card row-between" style="cursor:default">
            <span>${index + 1}. ${escapeHtml(row.name)}</span>
            <b>${row.avg_percent}% <span style="color:var(--color-text-muted);font-weight:400">(${row.attempts} ta)</span></b>
          </div>`
      )
      .join("") || "<p>Hali ma'lumot yo'q</p>";

  const withdrawalRows =
    withdrawalRequests
      .map(
        (request) => `
          <div class="card" style="cursor:default" data-withdrawal-id="${request.id}">
            <div class="card-body">
              <div class="row-between">
                <span>${escapeHtml(request.teacher)}</span>
                <b>${formatNumber(request.amount)} so'm</b>
              </div>
              <p>Karta: <b>${escapeHtml(request.card_number)}</b></p>
              <div class="row-between" style="margin-top:8px;gap:8px">
                <button class="btn" data-action="approve" style="padding:8px">✅ To'landi</button>
                <button class="btn btn-ghost" data-action="reject" style="padding:8px">❌ Rad etish</button>
              </div>
            </div>
          </div>`
      )
      .join("") || "<p>Kutilayotgan so'rovlar yo'q</p>";

  showPage(`
    ${pageHeader("👑 Admin")}
    <div class="stats-grid">
      <div class="stat-tile">👥<span class="stat-value">${stats.users}</span>Foydalanuvchilar</div>
      <div class="stat-tile">🧑‍🏫<span class="stat-value">${stats.teachers}</span>O'qituvchilar</div>
      <div class="stat-tile">📚<span class="stat-value">${stats.tests}</span>Testlar</div>
      <div class="stat-tile">✅<span class="stat-value">${stats.attempts}</span>Yakunlangan urinishlar</div>
    </div>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <h3>💰 Platforma daromadi</h3>
        <div class="row-between" style="margin-top:6px">
          <span>🎟️ Kredit sotuvi <span class="subtitle">(${formatNumber(stats.credits_sold)} dona)</span></span>
          <b>${formatNumber(stats.credits_revenue)} so'm</b>
        </div>
        <div class="row-between" style="margin-top:6px">
          <span>📝 Testlardan komissiya <span class="subtitle">(${stats.commission_percent}%)</span></span>
          <b>${formatNumber(stats.tests_commission)} so'm</b>
        </div>
        <div class="row-between" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--color-border)">
          <b>Jami</b>
          <b style="color:var(--color-primary)">${formatNumber(stats.revenue)} so'm</b>
        </div>
        ${
          stats.teachers_owed
            ? `<p class="subtitle" style="margin:10px 0 0">
                 ⚠️ O'qituvchilar balansida ${formatNumber(stats.teachers_owed)} so'm turibdi —
                 bu ular yechib olishi mumkin bo'lgan pul, sizniki emas.
               </p>`
            : ""
        }
      </div>
    </div>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <h3>💾 Zaxira nusxa</h3>
        <p>
          Butun baza JSON fayl bo'lib Telegram chatingizga keladi.
          Har kuni avtomatik yuboriladi — bu tugma esa hoziroq olish uchun.
        </p>
        <button class="btn btn-ghost" id="backup-button">💾 Hozir zaxira olish</button>
      </div>
    </div>
    <h3>🎁 Promo kodlar</h3>
    <div class="card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <p style="margin:0 0 8px">Kod yarating — o'qituvchi kiritsa, kredit oladi.</p>
        <input class="input" id="promo-new-code" placeholder="Kod, masalan BAHOR2026" maxlength="40" style="text-transform:uppercase" />
        <p class="subtitle" style="margin:4px 0 8px">O'qituvchi kodni «Kredit sotib olish» dagi son maydoniga yozadi. Faqat raqamli kod kamida 7 xonali bo'lsin.</p>
        <div class="two-fields">
          <input class="input" id="promo-new-credits" type="number" min="1" value="3000" placeholder="Kredit" />
          <input class="input" id="promo-new-per-teacher" type="number" min="1" max="100" value="3" placeholder="Har kim necha marta" />
        </div>
        <input class="input" id="promo-new-limit" type="number" min="1" placeholder="Jami necha marta (bo'sh — cheksiz)" />
        <label class="field-label" for="promo-new-expires">Amal qilish muddati (ixtiyoriy)</label>
        <input class="input" id="promo-new-expires" type="date" />
        <button class="btn" id="promo-create-button" style="margin-top:10px">➕ Promo kod yaratish</button>
        <p class="subtitle" style="margin:8px 0 0">🔒 Kod bazada yashirin saqlanadi — uni o'zingiz eslab qoling.</p>
      </div>
    </div>
    ${promoRows}
    <h3>💳 Karta orqali to'lovlar (tasdiq kutmoqda)</h3>
    ${cardTopUpRows}
    <h3>💸 Pul yechish so'rovlari</h3>
    ${withdrawalRows}
    <h3>🧑‍🏫 O'qituvchilar (eng ko'p to'lov qilgani birinchi)</h3>
    ${teacherRows}
    <h3>🏆 Eng yaxshi o'quvchilar (barcha testlar bo'yicha)</h3>
    ${topRows}
  `);

  document.querySelector('[data-action="back"]')?.addEventListener("click", loadHome);

  document.getElementById("promo-create-button").addEventListener(
    "click",
    guarded(async () => {
      const code = document.getElementById("promo-new-code").value.trim();
      if (code.length < 4) throw new Error("Kod kamida 4 belgidan iborat bo'lsin");
      const credits = Number(document.getElementById("promo-new-credits").value) || 3000;
      const limit = Number(document.getElementById("promo-new-limit").value) || null;
      const perTeacher = Number(document.getElementById("promo-new-per-teacher").value) || 3;
      const expires = document.getElementById("promo-new-expires").value || null;
      await api("/api/admin/promo-codes", { method: "POST", body: { code, credits, max_uses: limit, per_teacher_limit: perTeacher, expires_on: expires } });
      showToast(`✅ Promo kod yaratildi: +${formatNumber(credits)} kredit`);
      renderAdminDashboard();
    })
  );
  document.querySelectorAll("[data-promo-toggle]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(async () => {
        await api(`/api/admin/promo-codes/${button.dataset.promoToggle}/toggle`, { method: "POST", body: {} });
        renderAdminDashboard();
      })
    );
  });

  document.getElementById("backup-button").addEventListener(
    "click",
    guarded(async () => {
      if (telegram?.initData) {
        await api("/api/admin/backup", { method: "POST", body: {} });
        showToast("💾 Zaxira nusxa Telegramga yuborildi");
      } else {
        window.open("/api/admin/backup", "_blank");
      }
    })
  );

  document.querySelectorAll("[data-withdrawal-id]").forEach((card) => {
    const requestId = card.dataset.withdrawalId;
    card.querySelector('[data-action="approve"]').addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm("Pulni o'qituvchining kartasiga o'tkazdingizmi? \"To'landi\" deb belgilanadi."))) return;
        await api(`/api/admin/withdrawal-requests/${requestId}/approve`, { method: "POST", body: {} });
        showToast("✅ To'landi deb belgilandi");
        await renderAdminDashboard();
      })
    );
    card.querySelector('[data-action="reject"]').addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm("Pul yechish so'rovini rad etasizmi?"))) return;
        await api(`/api/admin/withdrawal-requests/${requestId}/reject`, { method: "POST", body: {} });
        showToast("❌ Rad etildi");
        await renderAdminDashboard();
      })
    );
  });

  document.querySelectorAll("[data-topup-id]").forEach((card) => {
    const topUpId = card.dataset.topupId;

    card.querySelector('[data-action="receipt"]')?.addEventListener(
      "click",
      guarded(async () => {
        // Chek himoyalangan manzilda — oddiy <img src> header yubora olmaydi,
        // shuning uchun rasm so'rov bilan olinib, ekranga qo'yiladi.
        const button = card.querySelector('[data-action="receipt"]');
        const image = card.querySelector("[data-receipt-image]");
        button.disabled = true;
        image.src = await fetchProtectedImage(`/api/admin/card-topups/${topUpId}/receipt`);
        image.hidden = false;
        button.hidden = true;
      })
    );

    card.querySelector('[data-action="approve"]').addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm("Kartangizga pul tushganini tekshirdingizmi? Kredit darhol yoziladi."))) return;
        await api(`/api/admin/card-topups/${topUpId}/approve`, { method: "POST", body: {} });
        showToast("✅ Kredit yozildi, o'qituvchiga xabar ketdi");
        await renderAdminDashboard();
      })
    );

    card.querySelector('[data-action="reject"]').addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm("To'lovni rad etasizmi? O'qituvchiga xabar boradi."))) return;
        await api(`/api/admin/card-topups/${topUpId}/reject`, { method: "POST", body: {} });
        showToast("❌ Rad etildi");
        await renderAdminDashboard();
      })
    );
  });
}

async function fetchProtectedImage(path) {
  // api() bilan bir xil autentifikatsiya header'lari, lekin javob JSON emas, rasm
  const headers = { "X-Init-Data": telegram?.initData || "" };
  if (!telegram?.initData) headers["X-Dev-User"] = DEV_USER_ID;

  const response = await fetch(path, { headers });
  if (!response.ok) throw new Error("Chek rasmini olib bo'lmadi");

  return URL.createObjectURL(await response.blob());
}

/* ---------- 3) O'quvchi oqimi ---------- */

/*
  Sahifani eslangan ma'lumot bilan darhol chizadi, keyin fonda yangisini
  so'raydi; o'zgargan bo'lsa va foydalanuvchi hali shu sahifada bo'lsa
  (marker element bor) — jimgina qayta chizadi. Birinchi marta esa odatdagidek
  kutib, keyin chizadi.
*/
async function renderWithCache(key, fetcher, draw, marker) {
  const cached = pageDataCache.get(key) || staleDataCache.get(key);
  if (cached) {
    draw(cached);
    startTopLoader(); // fonda yangilanayotganini bildiradi
    fetcher()
      .then((fresh) => {
        const changed = JSON.stringify(fresh) !== JSON.stringify(cached);
        pageDataCache.set(key, fresh);
        staleDataCache.delete(key);
        if (changed && document.querySelector(marker)) draw(fresh);
      })
      .catch(() => {})
      .finally(stopTopLoader);
    return;
  }

  const data = await fetcher();
  pageDataCache.set(key, data);
  staleDataCache.delete(key);
  draw(data);
}

// Ilova ochilgach fonda: bo'limlarga kirish birinchi martadayoq darhol bo'lsin
function prefetchSectionData() {
  fetchStudentHomeData().then((data) => pageDataCache.set("student-home", data)).catch(() => {});
  if (homeSnapshot?.is_teacher) {
    fetchTeacherDashboardData().then((data) => pageDataCache.set("teacher-dashboard", data)).catch(() => {});
  }
}

function fetchStudentHomeData() {
  return Promise.all([api("/api/me"), api("/api/me/attempts")]);
}

async function renderStudentHome() {
  await renderWithCache("student-home", fetchStudentHomeData, (data) => drawStudentHome(...data), "#find-test-button");
}

function drawStudentHome(me, attempts) {
  const inProgressRows =
    attempts.in_progress
      .map(
        (item) => `
          <div class="card card-entrance" data-resume-code="${item.code}">
            <div class="card-body">
              <h3>${escapeHtml(item.title)}</h3>
              <p>${escapeHtml(item.subject)} • Kod: ${item.code}</p>
            </div>
          </div>`
      )
      .join("") ||
    `<div class="card" style="cursor:default"><div class="card-body"><p>Hozircha sizga hech qanday test belgilanmagan.</p></div></div>`;

  const completedRows =
    attempts.completed
      .map(
        (item) => `
          <div class="card row-between" data-review-id="${item.attempt_id}" data-can-review="${item.can_review ? 1 : 0}">
            <span>${escapeHtml(item.title)}</span>
            ${
              item.can_review
                ? `<span class="badge badge-blue">📊 Natijani ko'rish ›</span>`
                : `<span class="badge badge-green">✅ Topshirildi</span>`
            }
          </div>`
      )
      .join("") ||
    `<div class="card" style="cursor:default"><div class="card-body"><p>Siz hali birorta ham testni tugatmagansiz.</p></div></div>`;

  showPage(`
    ${pageHeader("Testlar")}
    <div class="card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <div class="row-between">
          <h3>${escapeHtml(me.full_name)} <span style="color:var(--color-primary);font-weight:400">o'quvchi</span></h3>
          <button class="btn btn-ghost" id="edit-name-button" style="width:auto;padding:6px 10px">✏️</button>
        </div>
        <p>Telegram ID: <b>${me.tg_id}</b></p>
      </div>
    </div>
    <button class="btn" id="find-test-button">🔍 Yangi test qidirish</button>
    <h3>Jarayondagi testlar</h3>
    ${inProgressRows}
    <h3>Yakunlangan testlar</h3>
    ${completedRows}
  `);

  document.querySelector('[data-action="back"]')?.addEventListener("click", renderTestFolder);
  document.getElementById("find-test-button").addEventListener("click", renderTestCodeSearch);
  document.getElementById("edit-name-button").addEventListener(
    "click",
    guarded(() => renderEditName(me, renderStudentHome))
  );
  document.querySelectorAll("[data-resume-code]").forEach((card) => {
    card.addEventListener("click", guarded(() => startTest(card.dataset.resumeCode)));
  });
  // Yakunlangan test — natija faqat test tugagach ochiladi
  document.querySelectorAll("[data-review-id]").forEach((card) => {
    card.addEventListener(
      "click",
      guarded(async () => {
        if (card.dataset.canReview !== "1") {
          showToast("⏳ Natija test yakunlangach ochiladi");
          return;
        }
        await renderMyAttemptReview(Number(card.dataset.reviewId));
      })
    );
  });
}

/*
  O'quvchi o'zi topshirgan testni ko'radi (test yakunlangach): ball, daraja,
  o'rin, bo'limlar va har bir savolda nimani belgilagani / to'g'risi nima edi.
*/
async function renderMyAttemptReview(attemptId) {
  const review = await api(`/api/me/attempts/${attemptId}/review`);

  const rows = review.questions
    .map(
      (question) => `
        <div class="answer-row ${question.is_correct ? "" : "wrong"}">
          <b>${escapeHtml(question.label)}</b>
          <span>${question.is_correct ? "✅" : "❌"} ${escapeHtml(latexToPlain(question.chosen))}</span>
          ${question.is_correct ? "" : `<span class="right-answer">to'g'ri: ${escapeHtml(latexToPlain(question.correct))}</span>`}
        </div>`
    )
    .join("");

  const sections = Object.entries(review.sections || {});

  showPage(`
    <button class="back-button" data-action="back">←</button>
    <h2>${escapeHtml(review.title)}</h2>
    <p class="subtitle" style="margin-top:-6px">${escapeHtml(review.subject)}</p>
    <div class="stats-grid">
      ${review.ball != null ? `<div class="stat-tile"><span class="stat-value">${review.ball.toFixed(1)}</span>Ball</div>` : ""}
      ${review.grade ? `<div class="stat-tile"><span class="stat-value">${escapeHtml(review.grade)}</span>Daraja</div>` : ""}
      <div class="stat-tile"><span class="stat-value">${review.correct}/${review.total}</span>To'g'ri (${review.percent}%)</div>
      ${review.rank ? `<div class="stat-tile"><span class="stat-value">${review.rank}</span>O'rin (${review.participants} tadan)</div>` : ""}
    </div>
    ${
      sections.length
        ? `<p class="subtitle" style="margin:0 0 12px">${sections
            .map(([name, value]) => `${escapeHtml(name)}: <b>${value}%</b>`)
            .join(" · ")}</p>`
        : ""
    }
    <h3>Javoblaringiz</h3>
    ${rows}
  `);

  document.querySelector('[data-action="back"]').addEventListener("click", guarded(renderStudentHome));
}

function renderEditName(me, onDone) {
  const [currentFirst, ...rest] = (me.full_name || "").split(" ");
  const currentLast = rest.join(" ");

  showPage(`
    <button class="back-button" data-action="back">←</button>
    <h2>Ism-familiyani tahrirlash</h2>
    <input class="input" id="edit-first-name" placeholder="Ism" value="${escapeHtml(currentFirst || "")}" />
    <input class="input" id="edit-last-name" placeholder="Familiya" value="${escapeHtml(currentLast || "")}" />
    <button class="btn" id="save-name-button">✅ Saqlash</button>
  `);

  document.querySelector('[data-action="back"]').addEventListener("click", guarded(onDone));
  document.getElementById("save-name-button").addEventListener(
    "click",
    guarded(async () => {
      const first_name = document.getElementById("edit-first-name").value.trim();
      const last_name = document.getElementById("edit-last-name").value.trim();
      const result = await api("/api/me/name", { method: "POST", body: { first_name, last_name } });
      if (homeSnapshot) homeSnapshot.full_name = result.full_name;
      showToast("✅ Saqlandi");
      await onDone();
    })
  );
}

function renderTestCodeSearch() {
  showPage(`
    <button class="back-button" data-action="student-home">←</button>
    <h2>Test qidirish</h2>
    <input
      class="input"
      id="test-code-input"
      placeholder="Test kodini kiriting..."
      style="text-transform:uppercase;text-align:center;letter-spacing:2px;font-size:18px"
    />
    <button class="btn" id="search-button">🔍 Qidirish</button>
    <div id="search-result"></div>
  `);

  document.querySelector('[data-action="student-home"]').addEventListener("click", guarded(renderStudentHome));
  document.getElementById("search-button").addEventListener(
    "click",
    guarded(async () => {
      const code = document.getElementById("test-code-input").value.trim();
      const test = await api(`/api/tests/${encodeURIComponent(code)}`);
      renderTestFound(test);
    })
  );
}

function renderTestFound(test) {
  // Obuna ekranidagi "←" kabi boshqa sahifadan chaqirilganda qidiruv sahifasi
  // yo'q bo'ladi — ilgari shu sabab "Cannot set properties of null" xatosi chiqardi.
  if (!document.getElementById("search-result")) {
    renderTestCodeSearch();
    document.getElementById("test-code-input").value = test.code;
  }

  const priceLabel = test.price ? `${formatNumber(test.price)} so'm` : "Bepul";

  const now = new Date();
  let blockedMessage = "";

  if (test.enrollment_type === "closed") {
    blockedMessage = "🔒 Bu yopiq test — javoblarni o'qituvchining o'zi kiritadi.";
  } else if (test.start_time && now < parseUtc(test.start_time)) {
    blockedMessage = `⏳ Test hali boshlanmagan. Boshlanish vaqti: ${formatDateTime(test.start_time)}`;
  } else if (test.end_time && now > parseUtc(test.end_time)) {
    blockedMessage = "⌛ Test vaqti tugagan.";
  }

  const actionButton = blockedMessage
    ? `<button class="btn" disabled>▶️ TESTNI BOSHLASH</button>`
    : test.paid
      ? `<button class="btn" id="start-test-button">▶️ TESTNI BOSHLASH</button>`
      : studentPaymentHtml(test);

  document.getElementById("search-result").innerHTML = `
    <div class="card test-found" style="cursor:default">
      <div class="card-body" style="width:100%">
        <h3 style="margin:0">${escapeHtml(test.title)}</h3>
        <p class="subtitle" style="margin:4px 0 0">${test.count} ta savol · ${test.duration} daqiqa · ${priceLabel}</p>
        ${blockedMessage ? `<p class="test-blocked">${blockedMessage}</p>` : ""}
        ${channelRowsHtml(test)}
        ${actionButton}
      </div>
    </div>
  `;

  wireChannelRows(test);

  document.getElementById("start-test-button")?.addEventListener(
    "click",
    guarded(async () => {
      try {
        await startTest(test.code);
      } catch (error) {
        // Obuna yo'q — kanallar qatori yangilanadi (qaysi biri qolgani ko'rinadi)
        if (error.status === 451) {
          showToast("Avval kanallarga obuna bo'ling");
          testChannels(test).forEach((channel) => checkChannel(test, channel));
          return;
        }
        throw error; // 409 (allaqachon ishlagan), 503 (qayta urinish) — xabar sifatida
      }
    })
  );

  wireStudentCardPayment(test);

  document.querySelectorAll("[data-test-pay]").forEach((button) => button.addEventListener(
    "click",
    guarded(async () => {
      const payment = await api(`/api/tests/${test.code}/pay?provider=${button.dataset.testPay}`, { method: "POST", body: {} });

      if (payment.paid) {
        renderTestFound(await api(`/api/tests/${test.code}`));
        return;
      }

      // Payme'ning to'lov sahifasi ochiladi. To'lov o'tgani Payme'ning
      // webhook'i orqali serverga keladi, shuning uchun o'quvchi qaytib
      // kelganda "To'lovni tekshirish" tugmasi bilan holatni yangilaydi.
      openExternalLink(payment.checkout_url);
      showToast("💳 To'lov sahifasi ochildi. To'lagach, \"To'lovni tekshirish\" ni bosing");
    })
  ));

  document.getElementById("recheck-payment-button")?.addEventListener(
    "click",
    guarded(async () => {
      const fresh = await api(`/api/tests/${test.code}`);
      if (fresh.paid) {
        showToast("✅ To'lov tasdiqlandi");
      } else {
        showToast("To'lov hali tasdiqlanmadi, bir oz kutib qayta tekshiring");
      }
      renderTestFound(fresh);
    })
  );
}

/*
  Pullik test, hali to'lanmagan: o'quvchi o'qituvchi kartasiga pul o'tkazadi va
  chek yuboradi (o'qituvchi tasdiqlagach test ochiladi va botda xabar keladi).
  Payme/Click ulangan bo'lsa, ularning tugmalari ham chiqadi.
*/
function studentPaymentHtml(test) {
  const recheck = `<button class="btn btn-ghost" id="recheck-payment-button">🔄 To'lovni tekshirish</button>`;
  if (test.card_payment_pending) {
    return `<div class="notice">⏳ Chekingiz o'qituvchiga yuborildi. U kartasiga pul tushganini tekshirib tasdiqlagach, test ochiladi va botda xabar keladi.</div>${recheck}`;
  }

  const card = test.teacher_card;
  const cardBlock = card
    ? `<div class="panel" style="margin:10px 0 0">
         <p style="margin:0 0 6px"><b>1.</b> <b>${formatNumber(test.price)} so'm</b>ni o'qituvchi kartasiga o'tkazing:</p>
         <p style="margin:0"><span class="copy-code" data-copy="${escapeHtml(card.number.replace(/\s/g, ""))}" style="font-size:18px;letter-spacing:1px"><b>${escapeHtml(card.number)}</b>${COPY_ICON}</span></p>
         <p class="subtitle" style="margin:2px 0 10px">${escapeHtml(card.holder)}</p>
         <p style="margin:0 0 6px"><b>2.</b> To'lov chekining rasmini (skrinshot) tanlang:</p>
         <input id="test-receipt-input" type="file" accept="image/*" hidden />
         <label for="test-receipt-input" class="btn btn-ghost" id="test-receipt-label" style="display:block;text-align:center;cursor:pointer">🧾 Chek rasmini tanlash</label>
         <img id="test-receipt-preview" alt="" hidden style="max-width:100%;max-height:220px;border-radius:10px;margin:6px 0;display:block" />
         <p style="margin:8px 0 6px"><b>3.</b> Yuboring — o'qituvchi tasdiqlagach, test ochiladi.</p>
         <button class="btn" id="send-test-receipt-button">📤 Chekni yuborish</button>
       </div>`
    : "";
  const providerButtons = (test.payment_providers || [])
    .map(
      (provider) =>
        `<button class="btn btn-ghost" data-test-pay="${provider}">${provider === "click" ? "🔵 Click" : "🅿️ Payme"} orqali ${formatNumber(test.price)} so'm to'lash</button>`
    )
    .join("");

  if (!cardBlock && !providerButtons) {
    return `<div class="notice">O'qituvchi hali to'lov usulini sozlamagan — u bilan bog'laning.</div>`;
  }
  return `${cardBlock}${providerButtons}${providerButtons ? recheck : ""}`;
}

function wireStudentCardPayment(test) {
  const input = document.getElementById("test-receipt-input");
  if (!input) return;
  const preview = document.getElementById("test-receipt-preview");
  let receiptData = null;

  input.addEventListener(
    "change",
    guarded(async () => {
      const file = input.files?.[0];
      receiptData = null;
      preview.hidden = true;
      if (!file) return;
      if (!file.type.startsWith("image/")) {
        input.value = "";
        throw new Error("Chek rasm bo'lishi kerak (skrinshot yoki foto)");
      }
      receiptData = await compressImage(file);
      preview.src = receiptData;
      preview.hidden = false;
      document.getElementById("test-receipt-label").textContent = "🔄 Boshqa rasm tanlash";
    })
  );

  document.getElementById("send-test-receipt-button").addEventListener(
    "click",
    guarded(async () => {
      if (!receiptData) throw new Error("Avval chek rasmini tanlang");
      if (!(await askConfirm(`${formatNumber(test.price)} so'm uchun chek o'qituvchiga yuborilsinmi?`))) return;
      await api(`/api/tests/${test.code}/pay-card`, { method: "POST", body: { receipt_base64: receiptData } });
      showToast("✅ Chek yuborildi. O'qituvchi tasdiqlagach, botda xabar keladi");
      renderTestFound(await api(`/api/tests/${test.code}`));
    })
  );
}

/*
  Majburiy kanallar — har biri bitta ixcham qatorda va ALOHIDA tekshiriladi:
  holat belgisi, kanal nomi va o'z tugmasi. "Obuna bo'lish" bosilib Telegram'dan
  qaytilganda faqat o'sha kanal qayta tekshiriladi.
*/
function channelRowsHtml(test) {
  const channels = testChannels(test);
  if (!channels.length) return "";
  return `
    <div class="channel-rows">
      ${channels
        .map(
          (channel) => `
            <div class="channel-row" data-channel-row="${escapeHtml(channel)}">
              <span class="channel-dot wait" aria-hidden="true"></span>
              <span class="channel-name">${escapeHtml(channel)}</span>
              <span class="channel-action"><span class="subtitle" style="margin:0">tekshirilmoqda…</span></span>
            </div>`
        )
        .join("")}
    </div>`;
}

function wireChannelRows(test) {
  testChannels(test).forEach((channel) => checkChannel(test, channel));
}

// Bitta kanalni tekshiradi va uning qatorini yangilaydi
async function checkChannel(test, channel) {
  const row = [...document.querySelectorAll("[data-channel-row]")].find((item) => item.dataset.channelRow === channel);
  if (!row) return;
  let status = "unknown";
  try {
    const { channels } = await api(`/api/tests/${test.code}/subscriptions?channel=${encodeURIComponent(channel)}`);
    status = channels[0]?.status || "unknown";
  } catch (_) {
    /* tarmoq xatosi — "qayta tekshirish" taklif qilinadi */
  }
  if (!row.isConnected) return;
  const dot = row.querySelector(".channel-dot");
  const action = row.querySelector(".channel-action");
  dot.className = `channel-dot ${status === "yes" ? "ok" : status === "no" ? "bad" : "wait"}`;
  if (status === "yes") {
    action.innerHTML = `<span class="channel-ok">Obuna ✓</span>`;
  } else {
    action.innerHTML = `
      ${status === "no" && channelLink(channel) ? `<button type="button" class="share-pill" data-join>Obuna bo'lish</button>` : ""}
      <button type="button" class="share-pill" data-recheck aria-label="Qayta tekshirish">↻</button>`;
    action.querySelector("[data-join]")?.addEventListener("click", () => {
      openExternalLink(channelLink(channel));
      // Telegram'dan qaytganda — faqat shu kanal qayta tekshiriladi
      const onBack = () => {
        if (document.visibilityState !== "visible") return;
        document.removeEventListener("visibilitychange", onBack);
        checkChannel(test, channel);
      };
      document.addEventListener("visibilitychange", onBack);
    });
    action.querySelector("[data-recheck]").addEventListener("click", () => {
      action.innerHTML = `<span class="subtitle" style="margin:0">tekshirilmoqda…</span>`;
      checkChannel(test, channel);
    });
  }
}

// Testdagi barcha majburiy kanallar (adminlar kanali + o'qituvchi kanali)
function testChannels(test) {
  return test.required_channels || (test.required_channel ? [test.required_channel] : []);
}

function subscribeButtonsHtml(channels, className = "btn") {
  return channels
    .filter((channel) => channelLink(channel))
    .map((channel) => `<button class="${className}" data-subscribe="${escapeHtml(channel)}" style="margin-top:8px">📢 ${escapeHtml(channel)} — obuna bo'lish</button>`)
    .join("");
}

function wireSubscribeButtons() {
  document.querySelectorAll("[data-subscribe]").forEach((button) => {
    button.addEventListener("click", () => openExternalLink(channelLink(button.dataset.subscribe)));
  });
}

function channelLink(channel) {
  // "@kanal" -> havola. Raqamli ID'li yopiq guruhga havola yasab bo'lmaydi.
  const value = String(channel || "");
  return value.startsWith("@") ? `https://t.me/${value.slice(1)}` : "";
}

async function startTest(code) {
  const data = await api(`/api/tests/${code}/start`, { method: "POST", body: {} });

  // Javoblar ikki joyda saqlanadi: serverda va telefon xotirasida. Telefon
  // nusxasi yangiroq bo'lishi mumkin (oxirgi so'rov internet uzilgani uchun
  // yetib bormagan bo'lsa), shuning uchun u ustun turadi.
  const serverAnswers = data.answers || {};
  const localAnswers = readLocalAnswers(data.attempt_id);

  quizState = {
    attemptId: data.attempt_id,
    mode: data.mode,
    questions: data.questions,
    index: 0,
    answers: { ...serverAnswers, ...localAnswers },
    endsAt: Date.now() + data.remaining * 1000,
    code,
    finished: false,
    saveTimerId: null,
    savedSnapshot: JSON.stringify(serverAnswers),
  };

  const answeredCount = Object.keys(quizState.answers).length;
  if (answeredCount) {
    showToast(`↩️ Davom etamiz — ${answeredCount} ta javobingiz saqlanib qolgan`);
  }

  renderQuizQuestion();
  clearInterval(quizTimerId);
  quizTimerId = setInterval(() => guarded(updateQuizTimer)(), 1000);
}

/*
  Javoblarni serverda saqlash.

  Telegram Mini App juda oson yopiladi (pastga surish, qo'ng'iroq kelishi) —
  ilgari javoblar faqat "YAKUNLASH" bosilganda yuborilardi, ya'ni yarmida
  yopilsa o'quvchining butun mehnati yo'qolardi. Endi har bir o'zgarishdan
  keyin (bir oz kutib, bir yo'la) serverga yuboriladi.
*/
function answerStorageKey(attemptId) {
  return `quiz-answers-${attemptId}`;
}

function readLocalAnswers(attemptId) {
  // Telefon xotirasi yopiq bo'lishi mumkin (maxfiy oyna, cheklovlar) —
  // shuning uchun har bir murojaat himoyalangan.
  try {
    const stored = localStorage.getItem(answerStorageKey(attemptId));
    const parsed = stored ? JSON.parse(stored) : null;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function writeLocalAnswers(attemptId, answers) {
  try {
    localStorage.setItem(answerStorageKey(attemptId), JSON.stringify(answers));
  } catch (_) {
    /* joy yo'q yoki xotira yopiq — serverdagi nusxa yetarli */
  }
}

function clearLocalAnswers(attemptId) {
  try {
    localStorage.removeItem(answerStorageKey(attemptId));
  } catch (_) {
    /* ahamiyatsiz */
  }
}

function scheduleAnswerSave() {
  if (!quizState || quizState.finished) return;

  // Telefon xotirasiga DARHOL yoziladi: bu tezkor va internetga bog'liq emas,
  // ya'ni aloqa butunlay yo'q bo'lsa ham javoblar yo'qolmaydi.
  writeLocalAnswers(quizState.attemptId, quizState.answers);

  // Serverga esa bir oz kutib, bir yo'la yuboriladi — har bosishda alohida
  // so'rov yuborilsa, sekin internetda ilova sudralib qolardi.
  clearTimeout(quizState.saveTimerId);
  quizState.saveTimerId = setTimeout(() => saveAnswersNow(), 800);
}

async function saveAnswersNow({ keepalive = false } = {}) {
  if (!quizState || quizState.finished) return;

  const snapshot = JSON.stringify(quizState.answers);
  if (snapshot === quizState.savedSnapshot) return; // o'zgarish yo'q — so'rov yubormaymiz

  try {
    await api(`/api/attempts/${quizState.attemptId}/answers`, {
      method: "POST",
      body: { answers: quizState.answers },
      keepalive,
    });
    quizState.savedSnapshot = snapshot;
  } catch (_) {
    // Internet uzilgan bo'lishi mumkin — javoblar telefon xotirasida va
    // ekranda saqlanib turadi, keyingi o'zgarishda yana yuborishga urinadi.
    // O'quvchini bezovta qilmaymiz.
  }
}

function renderQuizQuestion() {
  if (quizState.mode === "key") {
    renderKeyQuizQuestion();
  } else {
    renderFullQuizQuestion();
  }
}

function renderFullQuizQuestion() {
  const question = quizState.questions[quizState.index];
  const total = quizState.questions.length;
  const isLast = quizState.index === total - 1;
  const progressPercent = Math.round(((quizState.index + 1) / total) * 100);

  const optionsHtml = ["A", "B", "C", "D"]
    .map((letter) => {
      const selected = quizState.answers[question.id] === letter ? "selected" : "";
      return `<div class="option ${selected}" data-letter="${letter}"><b>${letter})</b> ${escapeHtml(question[letter.toLowerCase()])}</div>`;
    })
    .join("");

  showPage(`
    <div class="row-between">
      <b>Savol ${quizState.index + 1} / ${total}</b>
      <b id="quiz-timer"></b>
    </div>
    <div class="progress-track"><div class="progress-fill" style="width:${progressPercent}%"></div></div>
    <h2>${escapeHtml(question.text)}</h2>
    <div id="options-container">${optionsHtml}</div>
    <button class="btn" id="next-button">${isLast ? "🏁 TESTNI YAKUNLASH" : "Keyingi →"}</button>
  `);

  setExitProtection(true); // test paytida Mini App tasodifan yopilmasin

  document.querySelectorAll("#options-container .option").forEach((optionEl) => {
    optionEl.addEventListener("click", () => {
      quizState.answers[question.id] = optionEl.dataset.letter;
      document.querySelectorAll("#options-container .option").forEach((el) => el.classList.remove("selected"));
      optionEl.classList.add("selected");
      scheduleAnswerSave();
    });
  });

  document.getElementById("next-button").addEventListener(
    "click",
    guarded(async () => {
      if (isLast) {
        if (!(await askConfirm("Testni yakunlaysizmi? Yakunlangach javoblarni o'zgartirib bo'lmaydi."))) return;
        saveAnswersNow();
        await finishQuiz();
      } else {
        // Keyingi savolga o'tishdan oldin javoblar serverga yetib boradi —
        // shu paytda ilova yopilib qolsa ham hech narsa yo'qolmaydi.
        saveAnswersNow();
        quizState.index += 1;
        renderQuizQuestion();
      }
    })
  );

  updateQuizTimer();
}

/*
  Kalit rejimidagi test — javob varaqasi ko'rinishida: barcha savollar bitta
  sahifada, "Keyingi" bosish shart emas. O'quvchi istalgan savolga qaytib,
  javobini o'zgartira oladi. Tepada taymer va nechta savol belgilangani turadi.
*/
function quizAnswerRefs() {
  return quizState.questions.flatMap((question) => (question.parts || [{ ref: question.number }]).map((part) => part.ref));
}

function updateAnsweredCounter() {
  const refs = quizAnswerRefs();
  const answered = refs.filter((ref) => String(quizState.answers[ref] || "").trim()).length;

  const counter = document.getElementById("answered-counter");
  if (counter) counter.textContent = `${answered} / ${refs.length} belgilandi`;

  const fill = document.getElementById("answered-progress");
  if (fill) fill.style.width = `${refs.length ? Math.round((answered / refs.length) * 100) : 0}%`;

  return { answered, total: refs.length };
}

function renderKeyQuizQuestion() {
  const rowsHtml = quizState.questions
    .map((question) => {
      const parts = question.parts || [{ ref: question.number, label: "" }];
      const isText = question.question_type === "text";
      const isMulti = question.question_type === "multi";

      if (isText) {
        const partsHtml = parts
          .map((part) => {
            const saved = quizState.answers[part.ref] || "";
            const partLabel = part.label ? `<span class="sheet-part">${part.label})</span>` : "";
            return `<div class="sheet-text-part">${partLabel}
              ${mathFieldHtml(saved, `data-ref="${part.ref}" placeholder="Javob"`)}
            </div>`;
          })
          .join("");

        return `<div class="sheet-row sheet-row-text">
          <span class="sheet-number">${question.number}</span>
          <div class="sheet-text-parts">${partsHtml}</div>
        </div>`;
      }

      return parts
        .map((part) => {
          const chosen = String(quizState.answers[part.ref] || "").split(",");
          const label = part.label ? `${question.number}${part.label}` : question.number;
          const buttons = Array.from({ length: question.option_count }, (_, index) => {
            const letter = String.fromCharCode(65 + index);
            return `<button type="button" class="sheet-option ${chosen.includes(letter) ? "selected" : ""}"
                            data-letter="${letter}">${letter}</button>`;
          }).join("");

          return `<div class="sheet-row">
            <span class="sheet-number">${label}</span>
            <div class="sheet-options" data-ref="${part.ref}" ${isMulti ? 'data-multi="1"' : ""}>${buttons}</div>
          </div>`;
        })
        .join("");
    })
    .join("");

  const hasText = quizState.questions.some((question) => question.question_type === "text");
  const hasMulti = quizState.questions.some((question) => question.question_type === "multi");

  showPage(`
    <div class="quiz-sticky">
      <div class="row-between">
        <b id="answered-counter"></b>
        <b id="quiz-timer"></b>
      </div>
      <div class="progress-track" style="margin-bottom:0"><div class="progress-fill" id="answered-progress"></div></div>
    </div>
    <p class="subtitle">Har bir savolning to'g'ri javobini belgilang. Javobni istalgan payt o'zgartirish mumkin.</p>
    ${hasMulti ? `<p class="subtitle" style="margin-top:0">Bir nechta javobli savollarda keraklilarining hammasini belgilang.</p>` : ""}
    <div class="panel answer-sheet">${rowsHtml}</div>
    ${hasText ? `<p class="subtitle answer-hint">${answerInputHint()}</p>` : ""}
    <button class="btn" id="finish-quiz-button" style="margin-top:16px">🏁 TESTNI YAKUNLASH</button>
  `);

  updateAnsweredCounter();
  setExitProtection(true); // test paytida Mini App tasodifan yopilmasin

  // Bitta tinglovchi butun varaq uchun — 45 ta savolda ham tez ishlaydi
  appElement.querySelector(".answer-sheet").addEventListener("click", (event) => {
    const button = event.target.closest(".sheet-option");
    if (!button) return;

    const group = button.closest(".sheet-options");
    const ref = group.dataset.ref;
    const letter = button.dataset.letter;

    if (group.dataset.multi) {
      button.classList.toggle("selected");
      quizState.answers[ref] = [...group.querySelectorAll(".sheet-option.selected")]
        .map((item) => item.dataset.letter)
        .join(",");
    } else if (button.classList.contains("selected")) {
      // Tanlangan javobni qayta bosish — belgini olib tashlaydi
      button.classList.remove("selected");
      quizState.answers[ref] = "";
    } else {
      group.querySelectorAll(".sheet-option").forEach((item) => item.classList.toggle("selected", item === button));
      quizState.answers[ref] = letter;
    }

    telegram?.HapticFeedback?.selectionChanged?.();
    updateAnsweredCounter();
    scheduleAnswerSave();
  });

  setupMathFields(appElement, (field, value) => {
    quizState.answers[field.dataset.ref] = value;
    updateAnsweredCounter();
    scheduleAnswerSave();
  });

  document.getElementById("finish-quiz-button").addEventListener(
    "click",
    guarded(async () => {
      // Har doim so'raladi — tasodifan bosib yuborilsa test yopilib qolmasin
      const { answered, total } = updateAnsweredCounter();
      const question =
        answered < total
          ? `${total - answered} ta savolga javob belgilanmagan. Baribir testni yakunlaysizmi?`
          : "Testni yakunlaysizmi? Yakunlangach javoblarni o'zgartirib bo'lmaydi.";
      if (!(await askConfirm(question))) return;
      saveAnswersNow();
      await finishQuiz();
    })
  );

  updateQuizTimer();
}

async function updateQuizTimer() {
  const timerEl = document.getElementById("quiz-timer");

  // Test ekranidan chiqib ketilgan bo'lsa, taymerni to'xtatamiz. Aks holda u
  // fonda ishlashda davom etib, vaqt tugaganda butunlay boshqa sahifani
  // natija ekraniga almashtirib yuborardi. Tashlab ketilgan urinishni server
  // o'zi tekshiradi (qarang: _finish_expired_attempts).
  if (!timerEl) {
    clearInterval(quizTimerId);
    return;
  }

  const secondsLeft = Math.max(0, Math.round((quizState.endsAt - Date.now()) / 1000));
  timerEl.textContent = "⏱️ " + formatDuration(secondsLeft);

  if (secondsLeft <= 0 && !quizState.finished) {
    await finishQuiz();
  }
}

async function finishQuiz() {
  if (quizState.finished) return;
  quizState.finished = true;

  clearInterval(quizTimerId);
  clearTimeout(quizState.saveTimerId);

  try {
    const result = await api(`/api/attempts/${quizState.attemptId}/finish`, {
      method: "POST",
      body: { answers: quizState.answers },
    });

    clearLocalAnswers(quizState.attemptId);
    renderQuizResult(result);
  } catch (error) {
    // Yakunlash o'tmasa (internet uzilgan bo'lsa), o'quvchi qaytib urinishi
    // kerak — shuning uchun "yakunlandi" belgisini orqaga qaytaramiz.
    quizState.finished = false;
    quizTimerId = setInterval(() => guarded(updateQuizTimer)(), 1000);
    throw error;
  }
}

/*
  Ilova yopilayotganda yoki fonga o'tayotganda javoblarni oxirgi marta
  yuboramiz. Telegram'da Mini App'ni pastga surib yopish eng ko'p uchraydigan
  holat — aynan shu paytda saqlanmagan javoblar yo'qolib ketardi.
*/
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveAnswersNow({ keepalive: true });
});

window.addEventListener("pagehide", () => saveAnswersNow({ keepalive: true }));

function renderQuizResult(result) {
  // Natija (ball, foiz) o'quvchiga ko'rsatilmaydi — u o'qituvchiga yuboriladi
  showPage(`
    <div class="result-number pop">✅</div>
    <h2 style="text-align:center">TEST YAKUNLANDI!</h2>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <p style="margin:0">Javoblaringiz qabul qilindi va o'qituvchingizga yuborildi.</p>
        <p class="subtitle" style="margin:8px 0 0">Natijani o'qituvchingizdan bilib olasiz.</p>
      </div>
    </div>
    <button class="btn" id="home-button">🏠 Bosh sahifa</button>
  `);

  document.getElementById("home-button").addEventListener("click", loadHome);
}

/* ---------- 4) O'qituvchi oqimi ---------- */

async function enterTeacherArea() {
  // Serverdan qayta so'raymiz: agar internet uzilgan bo'lsa, "ro'yxatdan o'ting"
  // degan ekranni ko'rsatib yubormaslik uchun. Ro'yxatdan o'tgan o'qituvchidan
  // hech qachon qayta ism-familiya so'ralmaydi.
  // Bosh sahifada allaqachon bilingan bo'lsa — qayta so'ramaymiz (darhol kirish)
  if (homeSnapshot && homeSnapshot.is_teacher) {
    isTeacher = true;
    await renderTeacherDashboard();
    return;
  }

  const me = await api("/api/me");
  isTeacher = Boolean(me.is_teacher);

  if (isTeacher) {
    await renderTeacherDashboard();
  } else {
    renderTeacherRegister();
  }
}

function renderTeacherRegister() {
  showPage(`
    <button class="back-button" data-action="home">←</button>
    <h2>O'qituvchi sifatida ro'yxatdan o'tish</h2>
    <p class="subtitle">
      Alohida login/parol kerak emas — Telegram orqali kim ekaningiz allaqachon aniq.
      Faqat ism-familiyangizni kiriting (natijalar va hisobotlarda shu ko'rinadi).
    </p>
    <input class="input" id="first-name-input" placeholder="Ism" />
    <input class="input" id="last-name-input" placeholder="Familiya" />
    <button class="btn" id="submit-register-button">✅ Ro'yxatdan o'tish</button>
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", renderTestFolder);
  document.getElementById("submit-register-button").addEventListener("click", guarded(submitTeacherRegister));
}

async function submitTeacherRegister() {
  const firstName = document.getElementById("first-name-input").value.trim();
  const lastName = document.getElementById("last-name-input").value.trim();

  await api("/api/teacher/register", { method: "POST", body: { first_name: firstName, last_name: lastName } });
  isTeacher = true;
  await renderTeacherDashboard();
}

function fetchTeacherDashboardData() {
  return Promise.all([api("/api/me"), api("/api/teacher/stats"), api("/api/teacher/tests")]);
}

async function renderTeacherDashboard() {
  try {
    await renderWithCache(
      "teacher-dashboard",
      fetchTeacherDashboardData,
      (data) => drawTeacherDashboard(...data),
      "#new-test-button"
    );
  } catch (error) {
    isTeacher = false;
    throw error;
  }
}

function drawTeacherDashboard(me, stats, tests) {
  showPage(`
    ${pageHeader("Testlar")}
    <div class="card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <div class="row-between">
          <h3>${escapeHtml(me.full_name)} <span style="color:var(--color-primary);font-weight:400">o'qituvchi</span></h3>
          <button class="btn btn-ghost" id="edit-name-button" style="width:auto;padding:6px 10px">✏️</button>
        </div>
        <p>Telegram ID: <b>${me.tg_id}</b></p>
      </div>
    </div>
    <div class="stats-grid">
      <div class="stat-tile">📚<span class="stat-value">${stats.tests}</span>Testlar</div>
      <div class="stat-tile">👥<span class="stat-value">${stats.students}</span>O'quvchilar</div>
      <div class="stat-tile">📊<span class="stat-value">${stats.avg}%</span>O'rtacha natija</div>
      <div class="stat-tile">🏆<span class="stat-value">${stats.max}%</span>Eng yuqori</div>
    </div>
    <div class="card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <div class="row-between">
          <span>💳 Pullik test to'lovlari</span>
          ${stats.pending_payments ? `<span class="badge badge-purple">${stats.pending_payments} ta chek kutmoqda</span>` : ""}
        </div>
        <p>${
          stats.card
            ? `O'quvchilar pulni kartangizga o'tkazadi: <b>${escapeHtml(stats.card.number)}</b>`
            : "Pullik test uchun karta raqamingizni kiriting — o'quvchilar pulni to'g'ridan-to'g'ri sizga o'tkazadi."
        }</p>
        <div class="contact-actions">
          <button class="btn ${stats.pending_payments ? "" : "btn-ghost"}" id="test-payments-button">🧾 Cheklar${stats.pending_payments ? ` (${stats.pending_payments})` : ""}</button>
          <button class="btn btn-ghost" id="card-settings-button">${stats.card ? "✏️ Karta" : "➕ Karta qo'shish"}</button>
        </div>
        ${
          stats.balance > 0
            ? `<div class="row-between" style="margin-top:10px"><span class="subtitle" style="margin:0">Payme/Click balansi: <b>${formatNumber(stats.balance)} so'm</b></span>
               <button class="btn btn-ghost" id="withdraw-button" style="width:auto;padding:6px 12px;margin:0">💸 Yechish</button></div>`
            : ""
        }
      </div>
    </div>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <div class="row-between"><span>🎫 Kredit balansi</span><b>${stats.credits} ta</b></div>
        <p>Har bir o'quvchi natijasi uchun 1 kredit sarflanadi (bepul testlarda)</p>
        <button class="btn btn-ghost" id="buy-credit-button">🛒 Kredit sotib olish</button>
      </div>
    </div>
    <button class="btn" id="new-test-button" style="margin-top:18px">➕ Yangi test yaratish</button>
    <div class="tabs">
      <button class="tab ${teacherTestsTab === "active" ? "active" : ""}" data-tab="active">Jarayondagi testlar</button>
      <button class="tab ${teacherTestsTab === "closed" ? "active" : ""}" data-tab="closed">Yopilgan testlar</button>
    </div>
    <div id="teacher-test-list"></div>
  `);

  wireTeacherChrome("tests");
  document.getElementById("edit-name-button").addEventListener(
    "click",
    guarded(() => renderEditName(me, renderTeacherDashboard))
  );
  document.getElementById("withdraw-button")?.addEventListener("click", guarded(renderWithdrawalForm));
  document.getElementById("test-payments-button").addEventListener("click", guarded(renderTestPaymentsPage));
  document.getElementById("card-settings-button").addEventListener("click", () => openCardSettingsSheet(renderTeacherDashboard));
  document.getElementById("buy-credit-button").addEventListener("click", guarded(renderCreditPurchaseForm));
  document.getElementById("new-test-button").addEventListener("click", () => renderNewTestForm("rash"));

  // Tab almashganda faqat ro'yxat qayta chiziladi — testlar allaqachon
  // yuklangan. Ilgari butun sahifa serverdan qayta olinib, 1–2 soniya
  // "yangilanib" turardi va sahifa tepaga sakrardi.
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (teacherTestsTab === tab.dataset.tab) return;
      teacherTestsTab = tab.dataset.tab;
      document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
      renderTeacherTestList(tests);
    });
  });

  renderTeacherTestList(tests);
}

/*
  O'qituvchining kartasi — pullik test pullari shu kartaga to'g'ridan-to'g'ri
  o'tkaziladi (platforma pulni ushlamaydi, kredit ishlatilmaydi).
*/
async function openCardSettingsSheet(onSaved) {
  const current = await api("/api/teacher/card");
  const formatCard = (value) => value.replace(/\D/g, "").slice(0, 16).replace(/(\d{4})(?=\d)/g, "$1 ");
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 4px">💳 Karta raqamingiz</h3>
    <p class="subtitle" style="margin:0 0 10px">Pullik testda o'quvchilar pulni shu kartaga o'tkazib, chek yuboradi. Siz botda tasdiqlaysiz.</p>
    <label class="field-label" style="margin:0">Karta raqami
      <input class="input" id="card-number-input" inputmode="numeric" autocomplete="cc-number" maxlength="19"
             placeholder="8600 1234 5678 9012" value="${escapeHtml(formatCard(current.card_number))}" style="margin:4px 0 0;letter-spacing:1px" /></label>
    <label class="field-label">Karta egasi (ism familiya)
      <input class="input" id="card-holder-input" maxlength="100" placeholder="Ali Valiyev"
             value="${escapeHtml(current.card_holder)}" style="margin:4px 0 0" /></label>
    <button class="btn" id="card-save-button" style="margin-top:12px">💾 Saqlash</button>
    <button class="btn btn-ghost" data-sheet-close>Bekor qilish</button>
  `);
  const numberInput = sheet.querySelector("#card-number-input");
  numberInput.addEventListener("input", () => {
    numberInput.value = formatCard(numberInput.value);
  });
  sheet.querySelector("#card-save-button").addEventListener(
    "click",
    guarded(async () => {
      const cardNumber = numberInput.value.replace(/\D/g, "");
      const cardHolder = sheet.querySelector("#card-holder-input").value.trim();
      if (cardNumber.length !== 16) throw new Error("Karta raqami 16 ta raqam bo'lishi kerak");
      if (cardHolder.length < 3) throw new Error("Karta egasining ism-familiyasini kiriting");
      await api("/api/teacher/card", { method: "PUT", body: { card_number: cardNumber, card_holder: cardHolder } });
      close();
      showToast("✅ Karta saqlandi");
      await onSaved?.();
    })
  );
}

/*
  O'quvchilar yuborgan cheklar. Asosan o'qituvchi chekni botda ko'rib, o'sha
  yerda ✅/❌ bosadi — bu sahifa zaxira yo'l (xabar o'tkazib yuborilgan bo'lsa).
*/
async function renderTestPaymentsPage() {
  const data = await api("/api/teacher/test-payments");
  const statusBadge = (status) =>
    status === "paid"
      ? `<span class="badge badge-green">✅ Tasdiqlangan</span>`
      : `<span class="badge badge-gray">❌ Rad etilgan</span>`;

  const pendingCards =
    data.pending
      .map(
        (item) => `
          <div class="card" style="cursor:default" data-payment-id="${item.id}">
            <div class="card-body" style="width:100%">
              <div class="row-between"><b>${escapeHtml(item.student)}</b><b>${formatNumber(item.amount)} so'm</b></div>
              <p class="subtitle" style="margin:2px 0 8px">«${escapeHtml(item.test_title)}» · ${formatDateTime(item.created_at)}</p>
              <img data-receipt-image alt="Chek" hidden style="max-width:100%;max-height:360px;border-radius:10px;margin-bottom:8px;display:block" />
              ${item.has_receipt ? `<button class="btn btn-ghost" data-action="receipt">🧾 Chekni ko'rish</button>` : ""}
              <div class="contact-actions">
                <button class="btn" data-action="approve">✅ Pul tushdi</button>
                <button class="btn btn-ghost" data-action="reject">❌ Rad etish</button>
              </div>
            </div>
          </div>`
      )
      .join("") || `<p class="subtitle">Tasdiq kutayotgan chek yo'q ✨</p>`;

  const recentRows = data.recent
    .map(
      (item) => `
        <div class="list-row">
          <div><b>${escapeHtml(item.student)}</b><p class="subtitle" style="margin:2px 0 0">«${escapeHtml(item.test_title)}» · ${formatNumber(item.amount)} so'm</p></div>
          ${statusBadge(item.status)}
        </div>`
    )
    .join("");

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2>🧾 Test to'lovlari</h2>
    <p class="subtitle" style="margin-top:-6px">Kartangizga pul tushganini tekshirib tasdiqlang — o'quvchiga test ochiladi va botda xabar boradi.</p>
    <h3>Tasdiq kutmoqda (${data.pending.length})</h3>
    ${pendingCards}
    ${recentRows ? `<h3>Oxirgi to'lovlar</h3>${recentRows}` : ""}
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));
  document.querySelectorAll("[data-payment-id]").forEach((card) => {
    const paymentId = card.dataset.paymentId;
    const name = card.querySelector("b").textContent;
    card.querySelector('[data-action="receipt"]')?.addEventListener(
      "click",
      guarded(async () => {
        const button = card.querySelector('[data-action="receipt"]');
        const image = card.querySelector("[data-receipt-image]");
        image.src = await fetchProtectedImage(`/api/teacher/test-payments/${paymentId}/receipt`);
        image.hidden = false;
        button.hidden = true;
      })
    );
    card.querySelector('[data-action="approve"]').addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm(`Kartangizga pul tushganini tekshirdingizmi? ${name}ga test ochiladi.`))) return;
        await api(`/api/teacher/test-payments/${paymentId}/approve`, { method: "POST", body: {} });
        showToast("✅ Tasdiqlandi — o'quvchiga test ochildi");
        await renderTestPaymentsPage();
      })
    );
    card.querySelector('[data-action="reject"]').addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm(`${name}ning to'lovi rad etilsinmi? Unga xabar boradi.`))) return;
        await api(`/api/teacher/test-payments/${paymentId}/reject`, { method: "POST", body: {} });
        showToast("❌ Rad etildi");
        await renderTestPaymentsPage();
      })
    );
  });
}

function renderTeacherTestList(tests) {
  const container = document.getElementById("teacher-test-list");
  if (!container) return;

  const visibleTests = tests.filter((test) => (teacherTestsTab === "closed" ? test.status === "closed" : test.status !== "closed"));

  container.innerHTML =
    visibleTests
      .map(
        (test) => `
          <div class="card card-entrance" data-test-id="${test.id}">
            <div class="card-body" style="width:100%">
              <div class="row-between" style="align-items:flex-start">
                <div>
                  <h3 style="margin-bottom:6px">
                    ${escapeHtml(test.title)}
                    ${testBadges(test)}
                  </h3>
                  <p><b>${escapeHtml(test.subject)}</b></p>
                  <p class="subtitle" style="margin:0">Test kodi: ${copyCodeChip(test.code)}</p>
                  <p class="subtitle" style="margin:0">Yaratilgan vaqti: ${formatDateTime(test.created_at)}</p>
                </div>
                <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
                  ${deleteButton("data-delete-test", test.id)}
                  <span style="color:var(--color-text-muted);font-size:22px">›</span>
                </div>
              </div>
            </div>
          </div>`
      )
      .join("") ||
    `<p class="subtitle">${teacherTestsTab === "closed" ? "Yopilgan testlar yo'q" : "Hali testlar yo'q"}</p>`;

  container.querySelectorAll("[data-test-id]").forEach((card) => {
    card.addEventListener("click", guarded(() => renderTestDetail(Number(card.dataset.testId))));
  });

  container.querySelectorAll("[data-delete-test]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(async (event) => {
        event.stopPropagation(); // kartaning o'zi ochilib ketmasin
        const testId = Number(button.dataset.deleteTest);
        const test = tests.find((item) => item.id === testId);

        const results = test.students ? ` Unda ${test.students} ta o'quvchi natijasi bor.` : "";
        if (!(await askConfirm(`"${test.title}" testi o'chirilsinmi?${results}`))) {
          return;
        }

        await api(`/api/teacher/tests/${testId}`, { method: "DELETE" });
        showToast("🗑 Test o'chirildi");
        await renderTeacherDashboard();
      })
    );
  });
}

async function renderWithdrawalForm() {
  const [balanceInfo, history] = await Promise.all([
    api("/api/teacher/balance"),
    api("/api/teacher/withdrawal-requests"),
  ]);

  const statusLabels = { pending: "⏳ Kutilmoqda", approved: "✅ To'landi", rejected: "❌ Rad etildi" };
  const historyRows =
    history
      .map(
        (request) => `
          <div class="card row-between" style="cursor:default">
            <span>${formatNumber(request.amount)} so'm</span>
            <b>${statusLabels[request.status]}</b>
          </div>`
      )
      .join("") || "<p>Hali so'rovlar yo'q</p>";

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2>Pul yechish</h2>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <h3>💸 Qanday ishlaydi</h3>
        <p>1. Qancha pul yechmoqchi ekaningizni va kartangiz raqamini kiriting.</p>
        <p>2. So'rov adminga yuboriladi.</p>
        <p>3. Admin pulni kartangizga o'tkazib, tasdiqlaydi — shunda balansingizdan yechiladi.</p>
      </div>
    </div>
    <p class="subtitle">Yechish mumkin bo'lgan balans: <b>${formatNumber(balanceInfo.balance)} so'm</b></p>
    <input class="input" id="withdrawal-amount-input" type="number" min="1000" step="1000" placeholder="Summa (so'm)" />
    <input class="input" id="withdrawal-card-input" placeholder="Karta raqamingiz (masalan 8600 1234 5678 9012)" />
    <button class="btn" id="submit-withdrawal-button">📨 So'rov yuborish</button>
    <h3>So'rovlar tarixi</h3>
    ${historyRows}
    ${supportCardHtml("Pul yechish bo'yicha savol bo'lsa — yozing")}
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));
  document.getElementById("submit-withdrawal-button").addEventListener(
    "click",
    guarded(async () => {
      const amount = Number(document.getElementById("withdrawal-amount-input").value) || 0;
      const cardNumber = document.getElementById("withdrawal-card-input").value.trim();

      if (amount <= 0) {
        showToast("To'g'ri summa kiriting");
        return;
      }
      if (cardNumber.length < 8) {
        showToast("Karta raqamini to'liq kiriting");
        return;
      }

      const confirmed = await askConfirm(
        `${formatNumber(amount)} so'm ${cardNumber} kartasiga yechib olinsinmi? Karta raqami to'g'riligini tekshiring.`
      );
      if (!confirmed) return;

      await api("/api/teacher/withdrawal-requests", { method: "POST", body: { amount, card_number: cardNumber } });
      showToast("✅ So'rov yuborildi, admin tasdiqlashini kuting");
      await renderWithdrawalForm();
    })
  );
}

async function renderCreditPurchaseForm() {
  const [balanceInfo, history] = await Promise.all([
    api("/api/teacher/balance"),
    api("/api/teacher/credit-purchases"),
  ]);

  const tierRows = balanceInfo.credit_price_tiers
    .map((tier) => {
      const range = tier.to ? `${tier.from}-${tier.to} ta` : `${tier.from}+ ta`;
      return `<p>${range} kredit: ${formatNumber(tier.price_per_credit)} so'm/kredit</p>`;
    })
    .join("");

  const statusLabels = {
    pending: "⏳ Tekshirilmoqda",
    paid: "✅ Qabul qilindi",
    cancelled: "❌ Rad etildi",
    failed: "⚠️ Chek yetib bormadi",
  };
  const methodIcons = { card: "💳", payme: "🅿️" };

  const historyRows =
    history
      .map(
        (purchase) => `
          <div class="card row-between" style="cursor:default">
            <span>${methodIcons[purchase.method] || ""} ${purchase.credits} ta kredit (${formatNumber(purchase.amount)} so'm)</span>
            <b>${statusLabels[purchase.status] || purchase.status}</b>
          </div>`
      )
      .join("") || "<p>Hali buyurtmalar yo'q</p>";

  // Kutilayotgan karta to'lovi bo'lsa, yangisini yuborib bo'lmaydi (server ham to'xtatadi)
  const hasPendingCard = history.some((purchase) => purchase.method === "card" && purchase.status === "pending");

  // Karta orqali to'lash: kartaga o'tkazish -> chek yuklash -> admin tasdiqlaydi
  const cardSection = balanceInfo.card_enabled
    ? `
      <div class="card" style="cursor:default">
        <div class="card-body" style="width:100%">
          <h3>💳 Karta orqali</h3>
          ${
            hasPendingCard
              ? `<p>⏳ Oldingi to'lovingiz tekshirilmoqda. Admin tasdiqlagach, kredit hisobingizga
                 o'zi tushadi va bot sizga xabar beradi.</p>`
              : `
                <p><b>1.</b> Quyidagi kartaga <b id="card-amount">—</b> o'tkazing:</p>
                ${copyCodeChip(escapeHtml(balanceInfo.admin_card))}
                <p style="margin-top:12px"><b>2.</b> To'lov chekining rasmini (skrinshot) tanlang:</p>
                <!-- Brauzerning o'z fayl tugmasi telefon tilida ("Выберите файл")
                     chiqadi — uni yashirib, o'rniga o'zimizning tugmani qo'yamiz -->
                <input id="receipt-input" type="file" accept="image/*" hidden />
                <label for="receipt-input" class="btn btn-ghost" id="receipt-label"
                       style="display:block;text-align:center;cursor:pointer">🧾 Chek rasmini tanlash</label>
                <img id="receipt-preview" alt="" hidden
                     style="max-width:100%;max-height:220px;border-radius:10px;margin:6px 0;display:block" />
                <p style="margin-top:8px"><b>3.</b> Yuboring — admin tekshirgach, kredit o'zi tushadi.</p>
                <button class="btn" id="send-receipt-button">📤 Chekni yuborish</button>`
          }
        </div>
      </div>`
    : "";

  // Avtomatik to'lov: karta ma'lumoti faqat to'lov tizimi sahifasida kiritiladi,
  // to'lov o'tishi bilan kredit o'zi tushadi
  const paymeSection = [
    balanceInfo.payme_enabled
      ? `<button class="btn ${balanceInfo.card_enabled ? "btn-ghost" : ""}" data-pay-provider="payme">🅿️ Payme orqali to'lash</button>`
      : "",
    balanceInfo.click_enabled
      ? `<button class="btn ${balanceInfo.card_enabled || balanceInfo.payme_enabled ? "btn-ghost" : ""}" data-pay-provider="click">🔵 Click orqali to'lash</button>`
      : "",
  ].join("");

  const noMethods =
    !balanceInfo.card_enabled && !balanceInfo.payme_enabled && !balanceInfo.click_enabled
      ? `<div class="card" style="cursor:default"><div class="card-body">
           <p>To'lov usullari hali sozlanmagan. Kredit olish uchun admin bilan bog'laning.</p>
         </div></div>`
      : "";

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2>Kredit sotib olish</h2>
    <p class="subtitle">Joriy kredit: <b>${formatNumber(balanceInfo.credits)} ta</b>. Necha dona sotib olmoqchisiz?</p>
    <input class="input" id="credit-count-input" type="text" inputmode="numeric" autocomplete="off"
           placeholder="Kredit miqdori" value="50" />
    <p class="subtitle" id="credit-price-line">Jami narx: <b id="credit-price-preview">—</b></p>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <h3>Narxlash tizimi</h3>
        ${tierRows}
      </div>
    </div>
    ${cardSection}
    ${paymeSection}
    ${noMethods}
    <h3>Buyurtmalar tarixi</h3>
    ${historyRows}
    ${supportCardHtml("To'lov yoki kredit bo'yicha muammo bo'lsa — yozing")}
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));

  const creditInput = document.getElementById("credit-count-input");
  const pricePreview = document.getElementById("credit-price-preview");
  const cardAmount = document.getElementById("card-amount");

  /*
    Yashirin promo kod: shu maydonga yoziladi. Hech qanday tugma yoki belgi
    chiqmaydi — yozuv promo kodga o'xshasa (harf qatnashsa yoki 7+ xonali son)
    va 5 soniya hech narsa yozilmasa, kod jimgina tekshiriladi. To'g'ri bo'lsa —
    kredit tushadi; noto'g'ri bo'lsa — hech narsa ko'rinmaydi.
  */
  const PROMO_IDLE_MS = 5000;
  const looksLikePromo = (value) => /[a-z]/i.test(value) || value.replace(/\s/g, "").length >= 7;
  let promoTimer = null;
  let lastTriedCode = "";
  const tryPromoSilently = async () => {
    const code = creditInput.value.trim();
    if (!looksLikePromo(code) || code === lastTriedCode || !creditInput.isConnected) return;
    lastTriedCode = code;
    try {
      const result = await api("/api/teacher/promo", { method: "POST", body: { code } });
      telegram?.HapticFeedback?.notificationOccurred?.("success");
      showToast(`🎉 +${formatNumber(result.credits_added)} kredit! Hozir: ${formatNumber(result.credits)} ta`);
      await renderCreditPurchaseForm();
    } catch (_) {
      /* noto'g'ri kod — jim; hech qanday ishora berilmaydi */
    }
  };
  creditInput.addEventListener("input", () => {
    clearTimeout(promoTimer);
    if (looksLikePromo(creditInput.value)) promoTimer = setTimeout(tryPromoSilently, PROMO_IDLE_MS);
  });

  // Narx sahifadagi jadval bo'yicha DARHOL hisoblanadi (har harfda serverga so'rov
  // ketmaydi). Server baribir buyurtmada narxni o'zi qayta hisoblaydi.
  const priceFor = (credits) => {
    const tier =
      balanceInfo.credit_price_tiers.find((item) => credits >= item.from && (item.to === null || credits <= item.to)) ||
      balanceInfo.credit_price_tiers[0];
    return credits * tier.price_per_credit;
  };

  const updatePricePreview = () => {
    const credits = Number(creditInput.value) || 0;
    if (credits < 1) {
      pricePreview.textContent = "—";
      if (cardAmount) cardAmount.textContent = "—";
      return;
    }
    if (credits > 100000) {
      pricePreview.textContent = "bir martada eng ko'pi 100 000 ta";
      if (cardAmount) cardAmount.textContent = "—";
      return;
    }
    const text = `${formatNumber(priceFor(credits))} so'm`;
    pricePreview.textContent = text;
    // Kartaga aynan shu summa o'tkazilishi kerak — admin chekni shu bilan solishtiradi
    if (cardAmount) cardAmount.textContent = text;
  };

  creditInput.addEventListener("input", updatePricePreview);
  updatePricePreview();

  const readCredits = () => {
    const credits = Number(creditInput.value) || 0;
    if (credits < 1) throw new Error("Kredit sonini kiriting");
    if (credits > 100000) throw new Error("Bir martada eng ko'pi 100 000 ta kredit");
    return credits;
  };

  // --- Karta orqali ---
  const receiptInput = document.getElementById("receipt-input");
  const receiptPreview = document.getElementById("receipt-preview");
  let receiptData = null;

  receiptInput?.addEventListener(
    "change",
    guarded(async () => {
      const file = receiptInput.files?.[0];
      receiptData = null;
      receiptPreview.hidden = true;
      if (!file) return;

      if (!file.type.startsWith("image/")) {
        receiptInput.value = "";
        throw new Error("Chek rasm bo'lishi kerak (skrinshot yoki foto)");
      }

      receiptData = await compressImage(file);
      receiptPreview.src = receiptData;
      receiptPreview.hidden = false;
      document.getElementById("receipt-label").textContent = "🔄 Boshqa rasm tanlash";
    })
  );

  document.getElementById("send-receipt-button")?.addEventListener(
    "click",
    guarded(async () => {
      const credits = readCredits();
      if (!receiptData) throw new Error("Avval chek rasmini tanlang");
      if (!(await askConfirm(`${credits} ta kredit uchun chek yuborilsinmi?`))) return;

      const button = document.getElementById("send-receipt-button");
      button.disabled = true;
      button.textContent = "⏳ Yuborilmoqda...";

      try {
        await api("/api/teacher/card-topups", {
          method: "POST",
          body: { credits, receipt_base64: receiptData },
        });
      } catch (error) {
        button.disabled = false;
        button.textContent = "📤 Chekni yuborish";
        throw error;
      }

      showToast("✅ Chek yuborildi. Admin tasdiqlagach, kredit o'zi tushadi");
      await renderCreditPurchaseForm();
    })
  );

  // --- Payme orqali ---
  document.querySelectorAll("[data-pay-provider]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(async () => {
        const credits = readCredits();
        const purchase = await api("/api/teacher/credit-purchases", {
          method: "POST",
          body: { credits, provider: button.dataset.payProvider },
        });
        openExternalLink(purchase.checkout_url);
        showToast("💳 To'lov sahifasi ochildi — to'lov o'tishi bilan kredit o'zi tushadi");
      })
    );
  });
}

/*
  Rasmni yuborishdan oldin kichraytiradi.

  Telefon kamerasidagi surat 4-8 MB bo'ladi — sekin internetda yuborish uzoq
  cho'ziladi va server chegarasidan oshib ketishi mumkin. Chekni o'qish uchun
  1600 piksel yetarli; JPEG ko'rinishida u odatda 200-500 KB bo'ladi.
*/
async function compressImage(file, maxSide = 1600, quality = 0.85) {
  const readAsDataUrl = () =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Rasm o'qilmadi"));
      reader.readAsDataURL(file);
    });

  const original = await readAsDataUrl();

  try {
    const image = await new Promise((resolve, reject) => {
      const element = new Image();
      element.onload = () => resolve(element);
      element.onerror = () => reject(new Error("Rasm ochilmadi"));
      element.src = original;
    });

    const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);

    return canvas.toDataURL("image/jpeg", quality);
  } catch (_) {
    // Kichraytirib bo'lmasa (eski brauzer), asl rasmning o'zi yuboriladi
    return original;
  }
}

const SUBJECTS = [
  "Matematika",
  "Fizika",
  "Kimyo",
  "Biologiya",
  "Ona tili va adabiyot",
  "Ingliz tili",
  "Rus tili",
  "Tarix",
  "Geografiya",
  "Informatika",
  "Huquq",
  "Boshqa fan",
];

// Milliy sertifikatda har bir fanning o'z tuzilishi bor. Bu yerda faqat
// farq qiladigan joyi yoziladi — qolgani Rash testning standartidan olinadi.
// "options" — qaysi savolda nechta variant bo'lishi ({savol: variantlar soni}).
const SUBJECT_PRESETS = {
  Matematika: {
    testCount: 35,
    options: { 33: 6, 34: 6, 35: 6 },
    textCount: 10,
    textParts: 2,
    note: "Matematika: 35 ta test (33-35 savollar A-F), 10 ta yozma (a, b)",
  },
};

function subjectStructure(subject, typePreset) {
  const preset = SUBJECT_PRESETS[subject] || {};
  return {
    testCount: preset.testCount ?? typePreset.testCount,
    textCount: preset.textCount ?? typePreset.textCount,
    textParts: preset.textParts ?? typePreset.textParts,
    options: preset.options || {},
    note: preset.note || "",
  };
}

// Test turlari. Ikkalasi ham "javoblar kaliti" usulida ishlaydi (savol matni
// o'qituvchining kanalida bo'ladi), farqi — standart savollar tuzilishida.
const TEST_TYPES = {
  rash: {
    label: "Rash test",
    testCount: 35,
    optionCount: 4,
    textCount: 10,
    textParts: 2, // har bir yozma savol a va b qismlardan iborat
    duration: 150,
    allowText: true,
    hint: "Milliy sertifikat uslubida: 35 ta variantli + 10 ta yozma (a, b) savol, 150 daqiqa.",
  },
  boshlangich: {
    label: "Boshlang'ich test",
    testCount: 30,
    optionCount: 4,
    textCount: 0,
    textParts: 1,
    duration: 60,
    allowText: false,
    hint: "Oddiy test: barcha savollar variantli (A/B/C/D).",
  },
};

function renderNewTestForm(testType, draft = {}) {
  const type = TEST_TYPES[testType] ? testType : "rash";
  const preset = TEST_TYPES[type];

  const selectOption = (value, label, selected) =>
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`;

  const subjectOptions = SUBJECTS.map((subject) => selectOption(subject, subject, draft.subject)).join("");

  // Fan bo'yicha tuzilish faqat Rash testda (Milliy sertifikat mockida) qo'llanadi
  const structure = type === "rash" ? subjectStructure(draft.subject || SUBJECTS[0], preset) : { ...preset, options: {}, note: "" };

  const header = `
    ${pageHeader("Yangi test")}

    <input class="input" id="field-title" placeholder="Test nomini kiriting" value="${escapeHtml(draft.title || "")}" />

    <label class="field-label" for="field-type">Test turi</label>
    <select class="input" id="field-type">
      ${Object.entries(TEST_TYPES)
        .map(([value, item]) => selectOption(value, item.label, type))
        .join("")}
    </select>
    <p class="subtitle" style="margin:-6px 0 0">${preset.hint}</p>

    <label class="field-label" for="field-subject">Fanni tanlang</label>
    <select class="input" id="field-subject">
      ${subjectOptions}
    </select>
    <p class="subtitle" id="subject-note" style="margin:-6px 0 0">${structure.note}</p>

    <div id="price-block">
    <p class="field-label" style="margin-top:14px">Test bepulmi yoki pullikmi?</p>
    <div class="row-between" id="price-toggle" data-value="${draft.paid ? "paid" : "free"}" style="gap:8px;margin-bottom:6px">
      <button type="button" class="btn ${draft.paid ? "btn-ghost" : ""}" id="price-free-button" style="flex:1">Bepul test</button>
      <button type="button" class="btn ${draft.paid ? "" : "btn-ghost"}" id="price-paid-button" style="flex:1">Pullik test</button>
    </div>
    <p class="subtitle" id="price-hint" style="margin-bottom:6px">
      ${
        draft.paid
          ? "O'quvchi to'lov qilgandan keyin testni ishlaydi"
          : "O'quvchilar bepul ishlaydi. Har bir natija uchun sizdan 1 kredit yechiladi."
      }
    </p>
    <div id="price-wrapper" style="display:${draft.paid ? "block" : "none"};margin-bottom:16px">
      <input class="input" id="field-price" type="number" min="0" step="1000"
             value="${draft.price ?? 0}" placeholder="Narxi (so'm)" style="margin:0" />
      <!-- O'quvchilar pulni o'qituvchi kartasiga o'tkazadi (pastda /api/teacher/card dan to'ldiriladi) -->
      <div class="row-between" style="margin-top:8px;gap:10px">
        <span class="subtitle" id="price-card-summary" style="margin:0;min-width:0">💳 Karta tekshirilmoqda…</span>
        <button type="button" class="btn btn-ghost" id="price-card-button" style="width:auto;padding:6px 12px;margin:0">Karta</button>
      </div>
    </div>
    </div>

    <p class="field-label" style="margin-top:14px">Testga qo'shilish turi</p>
    <div class="row-between" id="enrollment-toggle" data-value="${draft.enrollment || "open"}" style="gap:8px;margin-bottom:6px">
      <button type="button" class="btn ${draft.enrollment === "closed" ? "btn-ghost" : ""}" id="enrollment-open-button" style="flex:1">Ochiq test</button>
      <button type="button" class="btn ${draft.enrollment === "closed" ? "" : "btn-ghost"}" id="enrollment-closed-button" style="flex:1">Yopiq test</button>
    </div>
    <p class="subtitle" id="enrollment-hint" style="margin-bottom:16px">
      ${
        draft.enrollment === "closed"
          ? "O'qituvchi o'quvchilarni o'zi qo'shib, javoblarini kiritadi"
          : "O'quvchilar test kodini kiritib o'zlari qo'shiladi"
      }
    </p>

    <label class="field-label" for="field-start-time">Boshlanish vaqti (ixtiyoriy)</label>
    <input class="input" id="field-start-time" type="datetime-local" value="${draft.startTime || ""}" />

    <label class="field-label" for="field-end-time">Tugash vaqti (ixtiyoriy)</label>
    <input class="input" id="field-end-time" type="datetime-local" value="${draft.endTime || ""}" />

    <label class="field-label" for="field-duration">Davomiyligi (daqiqa)</label>
    <input class="input" id="field-duration" type="number" min="1" value="${draft.duration ?? preset.duration}" />
  `;

  const channelSettings = `
    <h3 style="margin-top:22px">Qo'shimcha sozlamalar</h3>
    <p class="subtitle" id="default-channels-note" style="margin:0 0 10px"></p>
    <div class="setting-card">
      <label class="setting-row">
        <input type="checkbox" id="field-require-channel" ${draft.channel ? "checked" : ""} />
        <span>
          <b>O'z kanalingizga ham majburiy obuna</b>
          <p class="subtitle" style="margin:4px 0 0">
            Yoqilsa, o'quvchi sizning kanalingiz yoki guruhingizga ham obuna bo'lmaguncha testni boshlay olmaydi.
          </p>
        </span>
      </label>
      <div id="channel-input-wrapper" style="display:${draft.channel ? "block" : "none"};margin-top:10px">
        <input class="input" id="field-channel" placeholder="@kanalingiz yoki -100… (guruh ID)" value="${escapeHtml(draft.channel || "")}" style="margin:0" />
        <p class="subtitle" style="margin:6px 0 0;color:var(--color-primary)">
          Avval botni shu kanal yoki guruhga <b>administrator</b> qilib qo'shing — saqlashda bot buni
          tekshiradi, admin bo'lmasa test saqlanmaydi.
        </p>
      </div>
    </div>
  `;

  const testCount = draft.testCount ?? structure.testCount;
  const optionCount = draft.optionCount ?? preset.optionCount;
  const textCount = draft.textCount ?? structure.textCount;

  const questionsSection = `
    <div class="panel">
      <div class="row-between" style="gap:8px;align-items:flex-end">
        <div style="flex:1">
          <label class="field-label" for="field-test-count">Test savollari</label>
          <input class="input" id="field-test-count" type="number" min="0" max="300" value="${testCount}" style="margin:0" />
        </div>
        <div style="flex:1">
          <label class="field-label" for="field-option-count">Variantlar</label>
          <input class="input" id="field-option-count" type="number" min="2" max="8" value="${optionCount}" style="margin:0" />
        </div>
        <div style="flex:1;display:${preset.allowText ? "block" : "none"}">
          <label class="field-label" for="field-text-count">Yozma</label>
          <input class="input" id="field-text-count" type="number" min="0" max="300" value="${textCount}" style="margin:0" />
        </div>
      </div>
      <div class="row-between" style="margin-top:10px">
        <span class="subtitle" style="margin:0" id="total-questions-hint"></span>
        <button class="btn btn-ghost" id="build-key-rows-button" style="width:auto;padding:8px 14px;margin:0">🔄 Yangilash</button>
      </div>
    </div>
    <div id="key-questions-container" data-default-parts="${structure.textParts}"
         data-option-overrides='${JSON.stringify(structure.options)}'></div>
    <div class="panel" style="margin-top:12px;padding:10px 14px">
      <div class="row-between" style="gap:10px">
        <span style="min-width:0"><b>🧩 Geometriya:</b> <span id="geometry-summary"></span></span>
        <button class="btn btn-ghost" id="pick-geometry-button" style="width:auto;padding:6px 12px;margin:0">Belgilash</button>
      </div>
    </div>`;

  showPage(`
    ${header}
    ${questionsSection}
    ${channelSettings}
    <div class="sticky-footer">
      <button class="btn" id="save-test-button" style="margin:0">Testni saqlash</button>
    </div>
  `);

  wireTeacherChrome("create");

  // Forma to'ldirila boshlansa — tasodifan chiqib ketishdan himoya yoqiladi
  // (sahifaning o'ziga ulanadi: #app boshqa sahifalarda ham qoladi)
  const formPage = appElement.querySelector(".page");
  formPage.addEventListener("input", markUnsaved);
  formPage.addEventListener("change", markUnsaved);

  // "Boshqa fan" tanlanmagan bo'lsa ham, avvalgi qiymat ro'yxatda bo'lmasligi mumkin
  const subjectSelect = document.getElementById("field-subject");
  if (draft.subject && !SUBJECTS.includes(draft.subject)) {
    subjectSelect.value = SUBJECTS[0];
  }

  // Fan almashtirilganda, o'sha fandagi Milliy sertifikat tuzilishi qo'llanadi
  subjectSelect.addEventListener("change", () => {
    if (type !== "rash") return;

    const next = subjectStructure(subjectSelect.value, preset);
    const container = document.getElementById("key-questions-container");

    document.getElementById("field-test-count").value = next.testCount;
    document.getElementById("field-text-count").value = next.textCount;
    container.dataset.defaultParts = next.textParts;
    container.dataset.optionOverrides = JSON.stringify(next.options);
    document.getElementById("subject-note").textContent = next.note;

    updateTotalHint();
    buildKeyQuestionRows(undefined, { resetOptions: true });
  });

  // Test turi o'zgartirilganda forma qaytadan chiziladi va savollar soni
  // yangi turning standartiga o'tadi. Nomi, fani kabi ma'lumotlar saqlanadi.
  document.getElementById("field-type").addEventListener("change", (event) => {
    const carried = collectTestFormDraft();
    delete carried.testCount;
    delete carried.optionCount;
    delete carried.textCount;
    delete carried.duration;
    delete carried.answers;
    renderNewTestForm(event.target.value, carried);
  });

  const priceToggle = document.getElementById("price-toggle");
  const priceFreeButton = document.getElementById("price-free-button");
  const pricePaidButton = document.getElementById("price-paid-button");
  const priceHint = document.getElementById("price-hint");
  const priceWrapper = document.getElementById("price-wrapper");

  priceFreeButton.addEventListener("click", () => {
    priceToggle.dataset.value = "free";
    priceFreeButton.classList.remove("btn-ghost");
    pricePaidButton.classList.add("btn-ghost");
    priceWrapper.style.display = "none";
    priceHint.textContent = "O'quvchilar bepul ishlaydi. Har bir natija uchun sizdan 1 kredit yechiladi.";
  });

  pricePaidButton.addEventListener("click", () => {
    priceToggle.dataset.value = "paid";
    pricePaidButton.classList.remove("btn-ghost");
    priceFreeButton.classList.add("btn-ghost");
    priceWrapper.style.display = "block";
    priceHint.textContent = "O'quvchi to'lov qilgandan keyin testni ishlaydi";
    selectOpenEnrollment(); // pullik test faqat ochiq bo'ladi
  });

  // Pullik test: o'quvchilar pulni shu kartaga o'tkazadi — karta bo'lmasa, shu yerda kiritiladi
  const priceCardSummary = document.getElementById("price-card-summary");
  const priceCardButton = document.getElementById("price-card-button");
  const refreshPriceCard = async () => {
    const card = await api("/api/teacher/card").catch(() => null);
    if (!priceCardSummary.isConnected) return;
    newTestHasCard = Boolean(card?.card_number);
    priceCardSummary.innerHTML = newTestHasCard
      ? `💳 Pul shu kartaga tushadi: <b>${escapeHtml(card.card_number.replace(/(\d{4})(?=\d)/g, "$1 "))}</b>`
      : `⚠️ Karta raqamingizni kiriting — o'quvchilar pulni unga o'tkazadi`;
    priceCardButton.textContent = newTestHasCard ? "✏️ O'zgartirish" : "➕ Kiritish";
    priceCardButton.classList.toggle("btn-ghost", newTestHasCard);
  };
  refreshPriceCard();
  priceCardButton.addEventListener("click", () => openCardSettingsSheet(refreshPriceCard));


  const enrollmentToggle = document.getElementById("enrollment-toggle");
  const enrollmentOpenButton = document.getElementById("enrollment-open-button");
  const enrollmentClosedButton = document.getElementById("enrollment-closed-button");
  const enrollmentHint = document.getElementById("enrollment-hint");

  function selectOpenEnrollment() {
    enrollmentToggle.dataset.value = "open";
    enrollmentOpenButton.classList.remove("btn-ghost");
    enrollmentClosedButton.classList.add("btn-ghost");
    enrollmentHint.textContent = "O'quvchilar test kodini kiritib o'zlari qo'shiladi";
  }

  enrollmentOpenButton.addEventListener("click", selectOpenEnrollment);
  enrollmentClosedButton.addEventListener("click", () => {
    // Pullik test faqat ochiq bo'ladi — o'quvchi o'zi to'lab, o'zi ishlaydi.
    if (priceToggle.dataset.value === "paid") {
      showToast("Pullik test faqat ochiq bo'ladi");
      return;
    }
    enrollmentToggle.dataset.value = "closed";
    enrollmentClosedButton.classList.remove("btn-ghost");
    enrollmentOpenButton.classList.add("btn-ghost");
    enrollmentHint.textContent = "O'qituvchi o'quvchilarni o'zi qo'shib, javoblarini kiritadi";
  });

  const channelCheckbox = document.getElementById("field-require-channel");
  channelCheckbox.addEventListener("change", () => {
    document.getElementById("channel-input-wrapper").style.display = channelCheckbox.checked ? "block" : "none";
  });

  // Adminlar kanali har bir testda majburiy — o'qituvchi buni oldindan bilsin
  api("/api/me")
    .then((me) => {
      const note = document.getElementById("default-channels-note");
      if (note && me.default_channels?.length) {
        note.innerHTML = `📢 Barcha testlarda majburiy obuna: <b>${me.default_channels.map(escapeHtml).join(", ")}</b>. Xohlasangiz, pastda o'z kanalingizni ham qo'shing.`;
      }
    })
    .catch(() => {});

  document.getElementById("save-test-button").addEventListener("click", guarded(saveNewTest));

  // Geometriya savollari — kalit qatorlaridagi savol raqamlari bo'yicha tanlanadi.
  // 45 savolli matematika testida o'qituvchi hech narsa bosmaydi: server Milliy
  // sertifikat bo'linishini (24–30, 33–35, 41–44) o'zi qo'llaydi — bu yerda faqat ko'rsatiladi.
  newTestSections = { text: "", picked: [], pickedName: "Geometriya", restName: "Algebra" };
  const geometrySummary = document.getElementById("geometry-summary");
  const geometryButton = document.getElementById("pick-geometry-button");
  const keyNumbers = () =>
    [...new Set(
      [...document.querySelectorAll("#key-questions-container .key-row")]
        .map((row) => parseInt(row.dataset.label, 10))
        .filter((number) => number > 0)
    )].sort((a, b) => a - b);
  const NATIONAL_MATH_GEOMETRY = [24, 25, 26, 27, 28, 29, 30, 33, 34, 35, 41, 42, 43, 44];
  const isNationalMath = () => keyNumbers().length === 45 && subjectSelect.value.toLowerCase().includes("matem");
  const refreshGeometrySummary = () => {
    if (newTestSections.text) {
      geometrySummary.textContent = compressNumbers(newTestSections.picked);
      geometryButton.textContent = "O'zgartirish";
    } else if (isNationalMath() && !newTestSections.cleared) {
      geometrySummary.textContent = "avtomatik ✅ (24–30, 33–35, 41–44)";
      geometryButton.textContent = "O'zgartirish";
    } else {
      geometrySummary.textContent = "yo'q";
      geometryButton.textContent = "Belgilash";
    }
  };
  refreshGeometrySummary();
  subjectSelect.addEventListener("change", refreshGeometrySummary);
  new MutationObserver(refreshGeometrySummary).observe(document.getElementById("key-questions-container"), { childList: true, subtree: true });
  geometryButton.addEventListener("click", () => {
    const numbers = keyNumbers();
    if (!numbers.length) {
      showToast("Avval savollar sonini kiriting");
      return;
    }
    openNumberPicker({
      numbers,
      // Avtomatik holatda oyna shablon bilan ochiladi — o'qituvchi faqat farqini o'zgartiradi
      picked: (newTestSections.text || newTestSections.cleared || !isNationalMath()
        ? newTestSections.picked
        : NATIONAL_MATH_GEOMETRY
      ).filter((number) => numbers.includes(number)),
      pickedName: newTestSections.pickedName,
      restName: newTestSections.restName,
      onSave: async (choice) => {
        // "Bo'limsiz" ataylab tanlansa — server shablonni ham qo'llamasligi uchun eslab qolinadi
        newTestSections = { ...choice, cleared: !choice.text };
        refreshGeometrySummary();
      },
    });
  });
  document.getElementById("build-key-rows-button").addEventListener("click", () => buildKeyQuestionRows());

  // Variantlar soni o'zgarishi bilan javoblar katakchalari darhol yangilanadi
  document.getElementById("field-option-count").addEventListener("input", (event) => {
    const value = Number(event.target.value);
    if (value >= 2 && value <= 8) buildKeyQuestionRows();
  });

  // "Jami 45 ta savol" degan eslatma sonlar o'zgarganda yangilanib turadi
  const updateTotalHint = () => {
    const total =
      (Number(document.getElementById("field-test-count").value) || 0) +
      (Number(document.getElementById("field-text-count").value) || 0);
    document.getElementById("total-questions-hint").textContent = `Jami ${total} ta savol`;
  };

  ["field-test-count", "field-text-count"].forEach((id) => {
    document.getElementById(id).addEventListener("input", updateTotalHint);
  });

  updateTotalHint();
  buildKeyQuestionRows(draft.answers);
}

function collectTestFormDraft() {
  const answers = {};
  document.querySelectorAll("#key-questions-container .key-row").forEach((row) => {
    answers[row.dataset.label] = { answer: row.dataset.answer || "", options: row.dataset.options };
  });

  return {
    title: document.getElementById("field-title").value,
    subject: document.getElementById("field-subject").value,
    enrollment: document.getElementById("enrollment-toggle").dataset.value,
    startTime: document.getElementById("field-start-time").value,
    endTime: document.getElementById("field-end-time").value,
    paid: document.getElementById("price-toggle").dataset.value === "paid",
    price: document.getElementById("field-price").value,
    duration: document.getElementById("field-duration").value,
    channel: document.getElementById("field-require-channel").checked
      ? document.getElementById("field-channel").value
      : "",
    testCount: document.getElementById("field-test-count").value,
    optionCount: document.getElementById("field-option-count").value,
    textCount: document.getElementById("field-text-count").value,
    answers,
  };
}

function buildKeyQuestionRows(savedAnswers, { resetOptions = false } = {}) {
  const container = document.getElementById("key-questions-container");
  const testCount = Math.max(0, Number(document.getElementById("field-test-count").value) || 0);
  const defaultOptions = Math.min(8, Math.max(2, Number(document.getElementById("field-option-count").value) || 4));
  const textCount = Math.max(0, Number(document.getElementById("field-text-count").value) || 0);
  const parts = Number(container.dataset.defaultParts) || 1;

  // Fan bo'yicha istisnolar: masalan matematikada 33-35 savollar A-F bo'ladi
  let optionOverrides = {};
  try {
    optionOverrides = JSON.parse(container.dataset.optionOverrides || "{}");
  } catch (_) {
    optionOverrides = {};
  }

  if (testCount + textCount === 0) {
    showToast("Kamida bitta savol bo'lsin");
    return;
  }

  // "Variantlar" soni o'zgargan bo'lsa, eski umumiy songa ega savollar yangi
  // songa o'tadi. Ilgari ular eski sonda (masalan, 4 ta — ABCD) qolib ketardi:
  // 3 yoki 6 kiritilsa ham hammasi ABCD bo'lib turardi. Fan bo'yicha alohida
  // sonli savollar (masalan, matematikada 33–35 — A–F) o'zgarmaydi.
  const previousDefault = Number(container.dataset.defaultOptions) || 0;
  container.dataset.defaultOptions = defaultOptions;

  // Savollar soni o'zgartirilganda belgilangan javoblar yo'qolmasin
  const previous = { ...(savedAnswers || {}) };
  container.querySelectorAll(".key-row").forEach((row) => {
    const variants = [...row.querySelectorAll(".math-input")].map(readMathValue);
    const followsDefault = Number(row.dataset.options) === previousDefault;
    previous[row.dataset.label] = {
      answer: row.dataset.answer || "",
      variants: variants.length ? variants : undefined,
      options: resetOptions || followsDefault ? undefined : row.dataset.options,
    };
  });

  // 1) Variantli savollar — ixcham katakchalar panjarasi.
  //    Katakchani bosganda javob A -> B -> C -> D -> A tarzida almashadi.
  const cells = [];
  for (let number = 1; number <= testCount; number += 1) {
    const saved = previous[number] || {};
    const options = Number(saved.options) || Number(optionOverrides[number]) || defaultOptions;
    // Variantlar kamaytirilsa (masalan, 4 dan 3 ga), endi yo'q harf ("D") qolmasin
    const savedAnswer = String(saved.answer || "A").toUpperCase();
    const fitsOptions = [...savedAnswer.replace(/,/g, "")].every((letter) => letter.charCodeAt(0) - 65 < options);
    const answer = fitsOptions ? savedAnswer : "A";

    cells.push(`
      <button class="answer-cell key-row" type="button" data-label="${number}" data-kind="letters"
              data-options="${options}" data-answer="${answer}">
        <span class="cell-number">${number}</span>
        <span class="cell-letter">${answer}</span>
      </button>`);
  }

  const lettersSection = testCount
    ? `<p class="section-label">1–${testCount}: TO'G'RI JAVOBLAR</p>
       <div class="panel">
         <div class="answer-grid">${cells.join("")}</div>
         <p class="subtitle" style="margin:10px 0 0">Javobni almashtirish uchun katakchani bosing.</p>
       </div>`
    : "";

  // 2) Yozma savollar — har bir qism alohida qatorda (36a, keyin 36b).
  //    Yonma-yon qilsak telefon ekranida tor bo'lib qolardi.
  const writtenRows = [];
  for (let index = 0; index < textCount; index += 1) {
    const questionNumber = testCount + index + 1;

    for (let partIndex = 0; partIndex < parts; partIndex += 1) {
      const label = parts > 1 ? `${questionNumber}${String.fromCharCode(97 + partIndex)}` : String(questionNumber);
      const savedRow = previous[label] || {};
      // Qoralamadan tiklanganda faqat "answer" ("0,5|1/2") bo'ladi — variantlarga ajratamiz
      const variants = savedRow.variants || (savedRow.answer ? String(savedRow.answer).split("|") : [""]);
      const isFirstPart = partIndex === 0;

      writtenRows.push(`
        <div class="written-row ${isFirstPart && index ? "written-row-gap" : ""}">
          <span class="written-number">${label}</span>
          <div class="written-cell key-row" data-label="${label}" data-kind="text"
               data-answer="${escapeHtml(variants.filter(Boolean).join("|"))}">
            ${variants.map((variant) => mathFieldHtml(variant, 'placeholder="javob"')).join("")}
          </div>
        </div>`);
    }
  }

  const writtenSection = textCount
    ? `<p class="section-label">${testCount + 1}–${testCount + textCount}: YOZMA JAVOBLAR</p>
       <div class="panel">
         ${writtenRows.join("")}
         <p class="subtitle answer-hint">${answerInputHint()}</p>
         <p class="subtitle" style="margin:4px 0 0">
           Bir nechta to'g'ri javob bo'lsa, | belgisi bilan ajrating: <b>0,5|1/2</b>
         </p>
       </div>`
    : "";

  container.innerHTML = lettersSection + writtenSection;

  container.removeEventListener("click", handleAnswerCellTap);
  container.addEventListener("click", handleAnswerCellTap);

  setupMathFields(container, (field) => {
    const cell = field.closest(".key-row");
    cell.dataset.answer = [...cell.querySelectorAll(".math-input")].map(readMathValue).filter(Boolean).join("|");
  });
}

function handleAnswerCellTap(event) {
  const cell = event.target.closest(".answer-cell");
  if (!cell) return;

  // Keyingi harfga o'tadi: A -> B -> C -> D -> A (variantlar soniga qarab)
  const options = Number(cell.dataset.options) || 4;
  const current = (cell.dataset.answer || "A").charCodeAt(0) - 65;
  const next = String.fromCharCode(65 + ((current + 1) % options));

  cell.dataset.answer = next;
  cell.querySelector(".cell-letter").textContent = next;
  telegram?.HapticFeedback?.selectionChanged?.();
}

// Test yaratish formasida tanlangan bo'limlar (saqlashda testga yoziladi)
let newTestSections = { text: "", picked: [], pickedName: "Geometriya", restName: "Algebra" };
let newTestHasCard = false; // pullik test uchun o'qituvchi kartasi kiritilganmi

async function saveNewTest() {
  const titleValue = document.getElementById("field-title").value.trim();
  if (titleValue.length < 2) {
    throw new Error("Test nomini kiriting");
  }

  const requireChannel = document.getElementById("field-require-channel").checked;
  const channelValue = document.getElementById("field-channel").value.trim();

  if (requireChannel && channelValue.length < 2) {
    throw new Error("Kanal nomini kiriting (masalan @kanalingiz)");
  }

  const isPaid = document.getElementById("price-toggle").dataset.value === "paid";
  const price = isPaid ? Number(document.getElementById("field-price").value) || 0 : 0;

  if (isPaid && price <= 0) {
    throw new Error("Pullik test uchun narxini kiriting");
  }
  if (isPaid && !newTestHasCard) {
    throw new Error("Pullik test uchun avval karta raqamingizni kiriting (narx ostidagi «➕ Kiritish»)");
  }

  const body = {
    title: titleValue,
    subject: document.getElementById("field-subject").value,
    topic: "",
    price,
    duration: Number(document.getElementById("field-duration").value) || 30,
    // Ikkala test turi ham javoblar kaliti usulida ishlaydi — savol matni
    // o'qituvchining kanalida bo'ladi, botga faqat javoblar kiritiladi.
    mode: "key",
    telegram_group_id: null,
    required_channel: requireChannel ? channelValue : null,
    enrollment_type: document.getElementById("enrollment-toggle").dataset.value,
    start_time: localTimeToUtcIso(document.getElementById("field-start-time").value),
    end_time: localTimeToUtcIso(document.getElementById("field-end-time").value),
    questions: [],
    key_questions: [],
  };

  const keyRows = [...document.querySelectorAll("#key-questions-container .key-row")];
  if (!keyRows.length) {
    throw new Error("Avval savollar sonini kiriting");
  }

  const emptyRow = keyRows.find((row) => !row.dataset.answer);
  if (emptyRow) {
    throw new Error(`${emptyRow.dataset.label}-savolning to'g'ri javobi belgilanmagan`);
  }

  body.key_questions = keyRows.map((row, index) => {
    const answer = row.dataset.answer;
    const isText = row.dataset.kind === "text";

    return {
      // "number" — ichki tartib raqami, "label" — ekranda ko'rinadigan nomi
      // ("36a" kabi). Oddiy savollarda label bo'sh qoladi.
      number: index + 1,
      label: row.dataset.label === String(index + 1) ? null : row.dataset.label,
      // Savol turi javobning o'zidan aniqlanadi: bitta harf — "single",
      // vergul bilan bir nechta harf — "multi", yozma katak — "text".
      question_type: isText ? "text" : answer.includes(",") ? "multi" : "single",
      option_count: isText ? 4 : Number(row.dataset.options) || 4,
      correct: answer,
    };
  });

  const result = await api("/api/teacher/tests", { method: "POST", body });
  // Bo'sh matn ("Bo'limsiz") ham yuboriladi — aks holda 45 savolli matematikada shablon qo'llanardi
  if (newTestSections.text || newTestSections.cleared) {
    try {
      await api(`/api/teacher/tests/${result.id}/sections`, { method: "PUT", body: { text: newTestSections.text } });
    } catch (_) {
      showToast("Bo'limlarni saqlab bo'lmadi — natijalar sahifasida qayta belgilang");
    }
  }
  showToast(`✅ Test yaratildi! Kodi: ${result.code}`);

  // Test yaratilgach, darhol shu testning sahifasi ochiladi — o'quvchi qo'shish,
  // javoblar varaqasini olish va yakunlash shu yerdan qilinadi.
  teacherTestsTab = "active";
  await renderTestDetail(result.id);
}

/* ---------- 4.1) Test sahifasi (yaratilgandan keyin ochiladi) ---------- */

/*
  Testni tahrirlash: nomi, davomiyligi, boshlanish/tugash vaqti.
  "+15 daq / +30 daq / +1 soat" — tugash vaqtini bir bosishda cho'zish
  (vaqt belgilanmagan bo'lsa — hozirdan boshlab). O'quvchilar ishlay
  boshlagan bo'lsa, davomiylikni faqat uzaytirish mumkin (server ham tekshiradi).
*/
function openTestEditSheet(test) {
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 10px">✏️ Testni tahrirlash</h3>
    <label class="field-label" style="margin:0">Test nomi
      <input class="input" id="edit-test-title" maxlength="200" value="${escapeHtml(test.title)}" style="margin:4px 0 0" /></label>
    <label class="field-label">Davomiyligi (daqiqa)
      <input class="input" id="edit-test-duration" type="number" min="${test.has_attempts ? test.duration : 1}" max="300"
             value="${test.duration}" style="margin:4px 0 0" /></label>
    ${test.has_attempts ? `<p class="subtitle" style="margin:-4px 0 6px;font-size:12px">O'quvchilar ishlay boshlagan — faqat uzaytirish mumkin</p>` : ""}
    <label class="field-label">Boshlanish vaqti
      <input class="input" id="edit-test-start" type="datetime-local" value="${utcIsoToLocalInput(test.start_time)}"
             ${test.has_attempts ? "disabled" : ""} style="margin:4px 0 0" /></label>
    <label class="field-label">Tugash vaqti
      <input class="input" id="edit-test-end" type="datetime-local" value="${utcIsoToLocalInput(test.end_time)}" style="margin:4px 0 0" /></label>
    <div class="contact-actions" style="margin-top:8px">
      <button type="button" class="share-pill" data-extend="15">+15 daq</button>
      <button type="button" class="share-pill" data-extend="30">+30 daq</button>
      <button type="button" class="share-pill" data-extend="60">+1 soat</button>
      <button type="button" class="share-pill" id="edit-test-no-end">Cheklovsiz</button>
    </div>
    <button class="btn" id="edit-test-save" style="margin-top:12px">💾 Saqlash</button>
    <button class="btn btn-ghost" data-sheet-close>Bekor qilish</button>
  `);

  const endInput = sheet.querySelector("#edit-test-end");
  sheet.querySelectorAll("[data-extend]").forEach((button) => {
    button.addEventListener("click", () => {
      // Tugash vaqti o'tib ketgan yoki yo'q bo'lsa — hozirdan boshlab cho'ziladi
      const current = endInput.value ? new Date(endInput.value) : null;
      const base = current && current > new Date() ? current : new Date();
      base.setMinutes(base.getMinutes() + Number(button.dataset.extend));
      endInput.value = utcIsoToLocalInput(base.toISOString());
      telegram?.HapticFeedback?.selectionChanged?.();
    });
  });
  sheet.querySelector("#edit-test-no-end").addEventListener("click", () => {
    endInput.value = "";
  });

  sheet.querySelector("#edit-test-save").addEventListener(
    "click",
    guarded(async () => {
      const body = {
        title: sheet.querySelector("#edit-test-title").value.trim(),
        duration: Number(sheet.querySelector("#edit-test-duration").value) || test.duration,
        end_time: localTimeToUtcIso(endInput.value),
      };
      if (!test.has_attempts) body.start_time = localTimeToUtcIso(sheet.querySelector("#edit-test-start").value);
      const saved = await api(`/api/teacher/tests/${test.id}`, { method: "PATCH", body });
      close();
      showToast(saved.reopened ? "✅ Saqlandi — test qayta ochildi" : "✅ Saqlandi");
      await renderTestDetail(test.id);
    })
  );
}

/*
  Katta ro'yxatlar (bitta testda minglab o'quvchi bo'lishi mumkin) bir yo'la
  chizilmaydi — telefon qotib qolardi. Avval 50 tasi, pastda "Yana ko'rsatish".
  Qidiruv esa butun ro'yxat bo'yicha ishlaydi (faqat chizilganlar emas).
  onRender — yangi chizilgan elementlarga tugma hodisalarini ulash uchun.
*/
function mountPagedList({ container, items, renderItem, emptyHtml = "", pageSize = 50, filterInput = null, matches = null, onRender = null }) {
  let filtered = items;
  let shown = 0;
  const more = document.createElement("button");
  more.type = "button";
  more.className = "btn btn-ghost";
  more.style.marginTop = "10px";
  (container.closest("table")?.parentElement || container).after(more);

  const renderNext = () => {
    const chunk = filtered.slice(shown, shown + pageSize);
    if (chunk.length) {
      const before = container.children.length;
      container.insertAdjacentHTML("beforeend", chunk.map(renderItem).join(""));
      onRender?.([...container.children].slice(before));
      shown += chunk.length;
    }
    const left = filtered.length - shown;
    more.hidden = left <= 0;
    more.textContent = `⬇️ Yana ${Math.min(left, pageSize)} ta ko'rsatish (jami ${filtered.length})`;
  };
  const reset = () => {
    container.innerHTML = filtered.length ? "" : emptyHtml;
    shown = 0;
    renderNext();
  };

  more.addEventListener("click", renderNext);
  filterInput?.addEventListener("input", () => {
    const query = filterInput.value.trim().toLowerCase();
    filtered = query ? items.filter((item) => matches(item, query)) : items;
    reset();
  });
  reset();
}

// Elementlar ichidan (o'zi ham) selector'ga mos keladiganlarni topadi
function findWithin(elements, selector) {
  return elements.flatMap((element) => [element, ...element.querySelectorAll(selector)].filter((node) => node.matches(selector)));
}

async function renderTestDetail(testId) {
  const test = await api(`/api/teacher/tests/${testId}`);
  const isClosedTest = test.enrollment_type === "closed";
  const isFinished = test.status === "closed";

  const studentRow = (student) => `
          <div class="list-row" data-student-id="${student.id}">
            <div ${student.has_answers ? `class="student-summary" data-detail="${student.id}"` : ""}>
              <b>${escapeHtml(student.name)}</b>
              <p class="subtitle" style="margin:2px 0 0">
                ${student.has_answers
            ? `✅ ${student.correct}/${test.count} • ${student.percent}% — batafsil ›`
            : "⏳ Javoblar kiritilmagan"
          }
              </p>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
              ${student.editable
            ? `${deleteButton("data-delete-student", student.id)}
                     <button class="icon-button" data-edit-student="${student.id}" title="Javoblarni kiritish">✏️</button>`
            : ""
          }
            </div>
          </div>`;

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2 style="text-align:center">${escapeHtml(test.title)}</h2>
    <div class="row-between" style="margin-bottom:14px">
      <div>${testBadges(test)}</div>
      <span class="subtitle" style="margin:0">Kod: ${copyCodeChip(test.code)}</span>
    </div>
    ${
      testChannels(test).length
        ? `<p class="subtitle">📢 Majburiy obuna: <b>${testChannels(test).map(escapeHtml).join(", ")}</b></p>`
        : ""
    }
    <p class="subtitle" style="margin-top:-4px">
      ⏱ ${test.duration} daqiqa${test.start_time ? ` · boshlanish: ${formatDateTime(test.start_time)}` : ""}${
        test.end_time ? ` · tugash: <b>${formatDateTime(test.end_time)}</b>` : ""
      }
    </p>
    <button class="btn btn-ghost" id="edit-test-button">✏️ Tahrirlash (nomi, vaqti)</button>

    ${
      isClosedTest
        ? `<button class="btn btn-ghost" id="answer-sheet-button">📄 Javoblar varaqasini olish</button>
           <button class="btn btn-ghost" id="answer-key-button">🔑 To'g'ri javoblar (kalit)</button>`
        : ""
    }
    ${isClosedTest && !isFinished ? `<button class="btn" id="add-students-button">👥 O'quvchilarni qo'shish</button>` : ""}
    <button class="btn btn-ghost" id="results-button">📊 Natijalarni ko'rish</button>
    ${isFinished
      ? `<button class="btn btn-ghost" id="reopen-button">🔓 Testni qayta ochish</button>`
      : `<button class="btn" id="finish-button">🏁 Yakunlash va natijalarni olish</button>`
    }

    ${test.missing > 0 && !isFinished
      ? `<div class="notice">Yakunlash uchun barcha o'quvchilar uchun javoblarni kiriting.
             ${test.missing} ta o'quvchi uchun javoblar kiritilmagan.</div>`
      : ""
    }

    <input class="input" id="student-filter-input" placeholder="🔍 O'quvchi ismi bo'yicha qidirish" />
    <h3>O'quvchilar (${test.students.length} ta)</h3>
    <div class="row-between subtitle" style="margin-bottom:10px">
      <span style="color:var(--color-primary)">Javoblari kiritilgan — ${test.filled} ta</span>
      <span style="color:#d97706">Kiritilmagan — ${test.missing} ta</span>
    </div>
    <div id="students-list"></div>
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));
  document.getElementById("edit-test-button").addEventListener("click", () => openTestEditSheet(test));

  document.getElementById("answer-sheet-button")?.addEventListener(
    "click",
    guarded(() =>
      deliverPdf(
        `/api/teacher/tests/${testId}/answer-sheet`,
        `${test.code}_javoblar_varaqasi.pdf`,
        "📄 Javoblar varaqasi Telegramga yuborildi"
      )
    )
  );

  document.getElementById("answer-key-button")?.addEventListener(
    "click",
    guarded(() =>
      deliverPdf(
        `/api/teacher/tests/${testId}/answer-sheet?answers=true`,
        `${test.code}_kalit.pdf`,
        "🔑 Javoblar kaliti Telegramga yuborildi"
      )
    )
  );

  document
    .getElementById("add-students-button")
    ?.addEventListener("click", guarded(() => renderAddStudentsForm(testId, test.title)));

  document.getElementById("results-button").addEventListener("click", guarded(() => renderTestResults(testId)));

  document.getElementById("reopen-button")?.addEventListener(
    "click",
    guarded(async () => {
      await api(`/api/teacher/tests/${testId}/reopen`, { method: "POST" });
      showToast("🔓 Test qayta ochildi");
      await renderTestDetail(testId);
    })
  );

  document.getElementById("finish-button")?.addEventListener(
    "click",
    guarded(async () => {
      if (!(await askConfirm("Test yakunlansinmi? Natijalar shundan keyin qotadi."))) return;
      await api(`/api/teacher/tests/${testId}/close`, { method: "POST" });
      showToast("🏁 Test yakunlandi");
      await renderTestResults(testId);
    })
  );

  const wireStudentRows = (elements) => {
  findWithin(elements, "[data-detail]").forEach((element) => {
    element.addEventListener(
      "click",
      guarded(() => renderStudentDetail(testId, Number(element.dataset.detail)))
    );
  });

  findWithin(elements, "[data-edit-student]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(() => renderStudentAnswersForm(testId, Number(button.dataset.editStudent)))
    );
  });

  findWithin(elements, "[data-delete-student]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(async () => {
        if (!(await askConfirm("Bu o'quvchi ro'yxatdan o'chirilsinmi?"))) return;
        await api(`/api/teacher/tests/${testId}/students/${button.dataset.deleteStudent}`, { method: "DELETE" });
        showToast("🗑 O'quvchi o'chirildi");
        await renderTestDetail(testId);
      })
    );
  });
  };

  mountPagedList({
    container: document.getElementById("students-list"),
    items: test.students,
    renderItem: studentRow,
    emptyHtml: `<p class="subtitle">Hali o'quvchi yo'q</p>`,
    filterInput: document.getElementById("student-filter-input"),
    matches: (student, query) => student.name.toLowerCase().includes(query),
    onRender: wireStudentRows,
  });
}

async function renderStudentDetail(testId, attemptId) {
  const detail = await api(`/api/teacher/tests/${testId}/students/${attemptId}/detail`);

  const rows = detail.questions
    .map(
      (question) => `
        <div class="answer-row ${question.is_correct ? "" : "wrong"}">
          <b>${escapeHtml(question.label)}</b>
          <span>${question.is_correct ? "✅" : "❌"} ${escapeHtml(latexToPlain(question.chosen))}</span>
          ${question.is_correct ? "" : `<span class="right-answer">to'g'ri: ${escapeHtml(latexToPlain(question.correct))}</span>`}
        </div>`
    )
    .join("");

  showPage(`
    <button class="back-button" data-action="test">←</button>
    <h2>${escapeHtml(detail.name)}</h2>
    <div class="stats-grid">
      <div class="stat-tile">
        <span class="stat-value">${detail.closed_correct}/${detail.closed_total}</span>Yopiq savollar
      </div>
      ${
        detail.written_total
          ? `<div class="stat-tile">
               <span class="stat-value">${detail.written_correct}/${detail.written_total}</span>Yozma javoblar
             </div>`
          : ""
      }
      <div class="stat-tile"><span class="stat-value">${detail.percent}%</span>Foiz</div>
    </div>
    <h3>Barcha javoblar</h3>
    ${rows}
  `);

  document.querySelector('[data-action="test"]').addEventListener("click", guarded(() => renderTestDetail(testId)));
}

async function renderAddStudentsForm(testId, testTitle) {
  showPage(`
    <button class="back-button" data-action="test">←</button>
    <h2>O'quvchilarni qo'shish</h2>
    <p class="subtitle">
      ${escapeHtml(testTitle)} — har bir o'quvchining ism-familiyasini alohida qatorga yozing.
      Javoblarini keyin, ro'yxatdagi ✏️ tugmasi orqali kiritasiz.
    </p>
    <textarea class="input" id="student-names-input" rows="8"
      placeholder="Ali Valiyev&#10;Zuhra Karimova&#10;Jasur Umarov"></textarea>
    <button class="btn" id="save-students-button">✅ Ro'yxatga qo'shish</button>
  `);

  document.querySelector('[data-action="test"]').addEventListener("click", guarded(() => renderTestDetail(testId)));

  document.getElementById("save-students-button").addEventListener(
    "click",
    guarded(async () => {
      const names = document
        .getElementById("student-names-input")
        .value.split("\n")
        .map((name) => name.trim())
        .filter((name) => name.length >= 2);

      if (!names.length) {
        showToast("Kamida bitta ism kiriting");
        return;
      }

      const result = await api(`/api/teacher/tests/${testId}/students`, { method: "POST", body: { names } });
      showToast(`✅ ${result.added} ta o'quvchi qo'shildi`);
      await renderTestDetail(testId);
    })
  );
}

async function renderStudentAnswersForm(testId, attemptId) {
  const [structure, saved] = await Promise.all([
    api(`/api/teacher/tests/${testId}/questions`),
    api(`/api/teacher/tests/${testId}/students/${attemptId}/answers`),
  ]);

  // Tanlangan javoblar shu yerda yig'iladi: {savol_ref: "B"} yoki {savol_ref: "A,C"}
  const answers = { ...saved.answers };

  const questionBlocks = structure.questions
    .map((question) => {
      const current = answers[question.ref] || "";

      const answerArea =
        question.question_type === "text"
          ? mathFieldHtml(current, `data-text-ref="${question.ref}" placeholder="Javobini yozing"`)
          : `<div class="answer-options" data-ref="${question.ref}"
                  style="display:grid;grid-template-columns:repeat(auto-fill,minmax(50px,1fr));gap:8px">
               ${Array.from({ length: question.option_count }, (_, index) => {
            const letter = String.fromCharCode(65 + index);
            const selected = current.split(",").includes(letter) ? "selected" : "";
            return `<div class="option ${selected}" data-letter="${letter}" style="text-align:center;margin:0;padding:12px"><b>${letter}</b></div>`;
          }).join("")}
             </div>`;

      return `
        <div class="question-block">
          <b>${escapeHtml(question.label)}</b>
          ${question.question_type === "multi" ? `<p class="subtitle" style="margin:4px 0">Bir nechta javob tanlash mumkin</p>` : ""}
          ${answerArea}
        </div>`;
    })
    .join("");

  showPage(`
    <button class="back-button" data-action="test">←</button>
    <h2>${escapeHtml(saved.name)}</h2>
    <p class="subtitle">O'quvchi varaqasida belgilangan javoblarni kiriting — bot avtomatik tekshiradi.</p>
    ${structure.questions.some((question) => question.question_type === "text")
      ? `<p class="subtitle answer-hint">${answerInputHint()}</p>`
      : ""}
    ${questionBlocks}
    <button class="btn" id="save-answers-button">✅ Saqlash va tekshirish</button>
  `);

  document.querySelector('[data-action="test"]').addEventListener("click", guarded(() => renderTestDetail(testId)));

  document.querySelectorAll(".answer-options").forEach((container) => {
    const ref = container.dataset.ref;
    const isMulti = structure.questions.find((item) => item.ref === ref).question_type === "multi";

    container.querySelectorAll(".option").forEach((optionEl) => {
      optionEl.addEventListener("click", () => {
        const letter = optionEl.dataset.letter;

        if (isMulti) {
          const selected = new Set((answers[ref] || "").split(",").filter(Boolean));
          if (selected.has(letter)) {
            selected.delete(letter);
          } else {
            selected.add(letter);
          }
          answers[ref] = [...selected].sort().join(",");
          optionEl.classList.toggle("selected");
        } else {
          answers[ref] = letter;
          container.querySelectorAll(".option").forEach((el) => el.classList.remove("selected"));
          optionEl.classList.add("selected");
        }
      });
    });
  });

  setupMathFields(appElement, (field, value) => {
    answers[field.dataset.textRef] = value;
  });

  document.getElementById("save-answers-button").addEventListener(
    "click",
    guarded(async () => {
      const result = await api(`/api/teacher/tests/${testId}/students/${attemptId}/answers`, {
        method: "POST",
        body: { answers },
      });

      showToast(`✅ ${result.name}: ${result.correct}/${result.total} = ${result.percent}%`);
      await renderTestDetail(testId);
    })
  );
}

async function renderResultsTab(selectedId) {
  const tests = await api("/api/teacher/tests");

  if (!tests.length) {
    showPage(`
      ${pageHeader("Natijalar")}
      <p class="subtitle">Hali test yaratilmagan. Avval test yarating.</p>
      `);
    wireTeacherChrome("results");
    return;
  }

  // Avval testlar ro'yxati: o'qituvchi qaysi testning natijasini ochayotganini
  // aniq ko'rsin. Ilgari birinchi test o'zi ochilib, boshqasini tanlash
  // ro'yxati oddiy matn maydoniga o'xshab qolgani uchun bilinmasdi.
  if (!selectedId) {
    renderResultsTestList(tests);
    return;
  }

  const testId = selectedId;
  const test = tests.find((item) => item.id === testId);
  if (!test) {
    renderResultsTestList(tests);
    return;
  }

  // Faqat natijalar so'raladi — ishtirokchilar soni va o'rtacha foiz shu
  // javobning o'zidan hisoblanadi, qo'shimcha so'rov kerak emas.
  const results = await api(`/api/teacher/tests/${testId}/results`);
  const summary = results.summary;

  // Pullik testda — Rasch darajalari (Milliy sertifikat), bepulda — 5 ballik baho
  const usesRasch = Boolean(results.uses_rasch);
  const buckets = (
    usesRasch
      ? [
          { label: "NC", from: 0, to: 46 },
          { label: "C", from: 46, to: 50 },
          { label: "C+", from: 50, to: 55 },
          { label: "B", from: 55, to: 60 },
          { label: "B+", from: 60, to: 65 },
          { label: "A", from: 65, to: 70 },
          { label: "A+", from: 70, to: 101 },
        ]
      : [
          { label: "2", from: 0, to: 56 },
          { label: "3", from: 56, to: 71 },
          { label: "4", from: 71, to: 86 },
          { label: "5", from: 86, to: 101 },
        ]
  ).map((bucket) => ({
    ...bucket,
    count: results.rows.filter((row) => {
      const value = usesRasch ? row.ball : row.percent;
      return value >= bucket.from && value < bucket.to;
    }).length,
  }));

  const maxCount = Math.max(1, ...buckets.map((bucket) => bucket.count));

  const chart = buckets
    .map(
      (bucket) => `
      <div class="chart-column">
        <span class="chart-value">${bucket.count}</span>
        <div class="chart-bar" style="height:${Math.max(4, (bucket.count / maxCount) * 96)}px"></div>
        <span class="chart-label">${bucket.label}</span>
      </div>`
    )
    .join("");

  const TOP_COUNT = 10;
  const rows = results.rows
    .slice(0, TOP_COUNT)
    .map((row) => resultLineHtml(row, results))
    .join("") || `<p class="subtitle" style="padding:14px">Hali natija yo'q</p>`;

  showPage(`
    ${pageHeader("Natijalar")}
    <button class="btn btn-ghost" id="all-results-button" style="width:auto;padding:8px 14px;margin:0 0 10px">
      ← Barcha testlar
    </button>
    <div class="card results-test-card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <h3 style="margin-bottom:6px">${escapeHtml(test.title)} ${testBadges(test)}</h3>
        <p style="margin:0"><b>${escapeHtml(test.subject)}</b> • Kod: ${copyCodeChip(test.code)}</p>
        <p class="subtitle" style="margin:4px 0 0">${test.count} ta savol • Yaratilgan: ${formatDateTime(test.created_at)}</p>
      </div>
    </div>

    <div class="panel">
      <div class="stats-grid" style="margin:0 0 16px">
        <div class="stat-tile"><span class="stat-value">${summary.participants}</span>Ishtirokchi</div>
        ${
          usesRasch
            ? `<div class="stat-tile"><span class="stat-value">${summary.avg_ball.toFixed(2)}</span>O'rtacha ball</div>
               <div class="stat-tile"><span class="stat-value">${summary.max_ball.toFixed(2)}</span>Eng yuqori ball</div>
               <div class="stat-tile"><span class="stat-value">${summary.top_count} ta</span>A+ va A</div>`
            : `<div class="stat-tile"><span class="stat-value">${summary.avg_percent}%</span>O'rtacha foiz</div>
               <div class="stat-tile"><span class="stat-value">${summary.max_percent}%</span>Eng yuqori</div>
               <div class="stat-tile"><span class="stat-value">${summary.top_count} ta</span>A'lo (5)</div>`
        }
      </div>
      <p class="section-label" style="margin:0 0 10px">${usesRasch ? "DARAJALAR (RASCH BALLI)" : "BAHOLAR (TO'G'RI JAVOBLAR FOIZI)"}</p>
      <div class="chart">${chart}</div>
    </div>

    <button class="btn" id="results-pdf-button" style="margin-top:14px">📄 PDF HISOBOT</button>
    <div class="contact-actions" style="margin-top:10px">
      <button class="btn btn-ghost" id="results-sections-button">🧩 Bo'limlar${results.section_names?.length ? ` (${results.section_names.length})` : ""}</button>
      <button class="btn btn-ghost" id="results-certificates-button">🎓 Sertifikatlar</button>
    </div>
    <button class="btn btn-ghost" id="results-send-students-button" style="margin-top:10px">📨 Sertifikatlarni o'quvchilarga yuborish</button>
    ${
      results.status === "closed"
        ? ""
        : `<p class="subtitle" style="margin:6px 4px 0;font-size:12px">🔒 Test yakunlangach yuborish mumkin bo'ladi</p>`
    }

    <div class="row-between" style="margin:18px 0 8px">
      <span class="section-label" style="margin:0">${results.rows.length > TOP_COUNT ? `TOP-${TOP_COUNT}` : "ISHTIROKCHILAR (O'RIN BO'YICHA)"}</span>
      <span class="subtitle" style="margin:0;font-size:12px">${usesRasch ? "ball • daraja" : "foiz • baho"}</span>
    </div>
    <div class="result-cards">${rows}</div>
    <div class="contact-actions" style="margin-top:10px">
      ${
        results.rows.length > TOP_COUNT
          ? `<button class="btn btn-ghost" id="all-participants-button">👥 Barchasi (${results.rows.length}) ›</button>`
          : ""
      }
      <button class="btn btn-ghost" id="question-stats-button">📊 Savol statistikasi ›</button>
    </div>
  `);

  wireTeacherChrome("results");

  document.getElementById("all-results-button").addEventListener("click", () => renderResultsTestList(tests));
  document.getElementById("results-pdf-button").addEventListener("click", guarded(() => downloadOrSendPdf(testId)));
  document.getElementById("results-certificates-button").addEventListener(
    "click",
    guarded(async () => {
      if (!results.rows.length) throw new Error("Hali natija yo'q");
      await deliverPdf(`/api/teacher/tests/${testId}/certificates`, "sertifikatlar.pdf", "🎓 Sertifikatlar Telegram chatga yuborildi");
    })
  );
  document.getElementById("results-sections-button").addEventListener("click", () => openSectionsSheet(testId, results));
  // Har bir o'quvchiga o'z sertifikati bot chatiga boradi — qaytarib bo'lmaydi, shuning uchun so'raladi
  document.getElementById("results-send-students-button").addEventListener(
    "click",
    guarded(async () => {
      if (results.status !== "closed") {
        throw new Error("Avval testni yakunlang (🏁 Yakunlash) — sertifikatlar faqat yakunlangan testdan yuboriladi");
      }
      if (!results.rows.length) throw new Error("Hali natija yo'q");
      const sure = await askConfirm(
        `${results.rows.length} ta o'quvchining har biriga o'z sertifikati (ball, foiz, daraja) bot orqali yuborilsinmi?`
      );
      if (!sure) return;
      const sent = await api(`/api/teacher/tests/${testId}/certificates/send-students`, { method: "POST" });
      showToast(
        `📨 ${sent.queued} ta o'quvchiga yuborilmoqda` +
          (sent.skipped ? ` (${sent.skipped} tasi Telegram'siz — o'tkazib yuborildi)` : "") +
          ". Tugagach, botda hisobot keladi."
      );
    })
  );

  document.querySelectorAll("[data-detail-id]").forEach((line) => {
    line.addEventListener(
      "click",
      guarded(() => renderStudentDetail(testId, Number(line.dataset.detailId)))
    );
  });
  const backToResults = () => renderResultsTab(testId);
  document.getElementById("all-participants-button")?.addEventListener("click", () => renderParticipantsPage(testId, results, backToResults));
  document.getElementById("question-stats-button").addEventListener(
    "click",
    guarded(() => renderQuestionStatsPage(testId, results.mode, backToResults))
  );
}

// Natijalar ro'yxatidagi bitta qator (o'rin, ism, bo'limlar/foiz, ball va daraja)
function resultLineHtml(row, results) {
  const usesRasch = Boolean(results.uses_rasch);
  return `
      <div class="result-line" data-detail-id="${row.attempt_id}">
        <span class="result-rank">${row.rank}</span>
        <span class="result-name">
          ${escapeHtml(row.name)}
          <small class="result-meta">${
            results.section_names?.length
              ? results.section_names.map((name) => `${escapeHtml(name)}: ${row.sections[name]}%`).join(" • ")
              : `✅ ${row.correct} • ❌ ${row.wrong} • ${row.percent}%`
          }</small>
        </span>
        <span class="result-percent">${usesRasch ? row.ball.toFixed(2) : `${row.percent}%`}<small class="result-grade">${usesRasch ? escapeHtml(row.grade) : `baho ${escapeHtml(row.grade)}`}</small></span>
      </div>`;
}

// Barcha ishtirokchilar — alohida sahifa: qidiruv va 50 tadan
function renderParticipantsPage(testId, results, onBack) {
  showPage(`
    <button class="back-button" data-action="back">←</button>
    <h2>${escapeHtml(results.title)} — ishtirokchilar (${results.rows.length})</h2>
    <input class="input" id="participants-filter" placeholder="🔍 O'quvchini qidirish..." />
    <div class="result-cards" id="participants-list"></div>
  `);
  document.querySelector('[data-action="back"]').addEventListener("click", guarded(onBack));
  mountPagedList({
    container: document.getElementById("participants-list"),
    items: results.rows,
    renderItem: (row) => resultLineHtml(row, results),
    emptyHtml: `<p class="subtitle" style="padding:14px">Hech kim topilmadi</p>`,
    filterInput: document.getElementById("participants-filter"),
    matches: (row, query) => row.name.toLowerCase().includes(query),
    onRender: (elements) =>
      findWithin(elements, "[data-detail-id]").forEach((line) =>
        line.addEventListener("click", guarded(() => renderStudentDetail(testId, Number(line.dataset.detailId))))
      ),
  });
}

// Savol statistikasi — alohida sahifa. Ko'p o'quvchida eng foydalisi: qaysi savolda ko'p xato
async function renderQuestionStatsPage(testId, mode, onBack) {
  const stats = await api(`/api/teacher/tests/${testId}/stats`);
  const questions = stats.questions.map((q, index) => ({
    ...q,
    label: mode === "key" ? q.text : `${index + 1}. ${q.text}`,
    order: index,
    wrongShare: q.total ? q.wrong / q.total : 0,
  }));
  let sortByWrong = false;

  const draw = () => {
    const list = [...questions].sort((a, b) => (sortByWrong ? b.wrongShare - a.wrongShare : a.order - b.order));
    document.getElementById("question-stats-list").innerHTML = list
      .map(
        (q) => `
          <div class="question-stat">
            <span class="question-stat-label">${escapeHtml(q.label)}</span>
            <span class="question-stat-count"><span class="stat-dot is-correct"></span>${q.total - q.wrong}</span>
            <span class="question-stat-count"><span class="stat-dot is-wrong"></span>${q.wrong}</span>
            <span class="question-stat-share">${Math.round(q.wrongShare * 100)}%</span>
          </div>`
      )
      .join("") || `<p class="subtitle">Hali natija yo'q</p>`;
    document.getElementById("question-stats-sort").textContent = sortByWrong ? "↕️ Raqam bo'yicha" : "🔥 Eng ko'p xato tepada";
  };

  showPage(`
    <button class="back-button" data-action="back">←</button>
    <h2>Savol statistikasi</h2>
    <div class="row-between" style="margin-bottom:10px">
      <span class="subtitle" style="margin:0">${stats.total ?? ""} ta ishtirokchi</span>
      <button class="share-pill" id="question-stats-sort"></button>
    </div>
    <div class="question-stats">
      <div class="question-stats-legend"><span class="stat-dot is-correct"></span>to'g'ri <span class="stat-dot is-wrong"></span>xato · % — xato qilganlar ulushi</div>
      <div id="question-stats-list"></div>
    </div>
  `);
  document.querySelector('[data-action="back"]').addEventListener("click", guarded(onBack));
  document.getElementById("question-stats-sort").addEventListener("click", () => {
    sortByWrong = !sortByWrong;
    draw();
  });
  draw();
}

/*
  Bo'limlar: savollarni Algebra / Geometriya kabi qismlarga ajratish.
  Natijalar, PDF va sertifikatda har bo'lim alohida ko'rsatiladi.
*/
// Raqamlar ro'yxatini qisqa yozuvga: [1,2,3,7,9,10] → "1-3, 7, 9-10"
function compressNumbers(numbers) {
  const parts = [];
  let start = null;
  numbers.forEach((number, index) => {
    if (start === null) start = number;
    if (index + 1 === numbers.length || numbers[index + 1] !== number + 1) {
      parts.push(start === number ? `${number}` : `${start}-${number}`);
      start = null;
    }
  });
  return parts.join(", ");
}

/*
  Bo'limlar — tugmachalar bilan: o'qituvchi geometriya savollarini bosib
  belgilaydi, belgilanmaganlari Algebra bo'ladi. Bu oyna test YARATISHDA ham,
  natijalar sahifasida ham ishlatiladi.
*/
function openNumberPicker({ numbers, picked: initial = [], pickedName = "Geometriya", restName = "Algebra", onSave, allowClear = true }) {
  const picked = new Set(initial);
  const { sheet, close } = openSheet(`
    <h3 style="margin:0 0 4px">🧩 Bo'limlar</h3>
    <p class="subtitle" style="margin:0 0 10px">Geometriya savollarini bosib belgilang — qolganlari o'zi Algebra bo'ladi.</p>
    <div class="two-fields">
      <label class="field-label" style="margin:0">Belgilanganlar
        <input class="input" id="picked-name" maxlength="30" value="${escapeHtml(pickedName)}" style="margin:4px 0 0" /></label>
      <label class="field-label" style="margin:0">Qolganlari
        <input class="input" id="rest-name" maxlength="30" value="${escapeHtml(restName)}" style="margin:4px 0 0" /></label>
    </div>
    ${
      numbers.length >= 44
        ? `<button type="button" class="share-pill" id="sections-preset" style="margin-top:10px">📐 Milliy sertifikat (matematika): 24–30, 33–35, 41–44</button>`
        : ""
    }
    <div class="number-grid" id="number-grid">
      ${numbers
        .map((number) => `<button type="button" class="number-chip${picked.has(number) ? " is-picked" : ""}" data-number="${number}">${number}</button>`)
        .join("")}
    </div>
    <p class="subtitle" id="sections-summary" style="margin:8px 0 0"></p>
    <button class="btn" id="sections-save" style="margin-top:10px">💾 Saqlash</button>
    <div class="contact-actions" style="margin-top:8px">
      ${allowClear ? `<button class="btn btn-ghost" id="sections-clear">Bo'limsiz</button>` : ""}
      <button class="btn btn-ghost" data-sheet-close>Yopish</button>
    </div>
  `);

  const summary = sheet.querySelector("#sections-summary");
  const pickedInput = sheet.querySelector("#picked-name");
  const restInput = sheet.querySelector("#rest-name");
  const chips = () => sheet.querySelectorAll("[data-number]");
  const updateSummary = () => {
    summary.textContent = picked.size
      ? `${pickedInput.value.trim() || "Geometriya"}: ${picked.size} ta savol · ${restInput.value.trim() || "Algebra"}: ${numbers.length - picked.size} ta`
      : "Hali hech qaysi savol belgilanmagan";
  };
  sheet.querySelector("#number-grid").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-number]");
    if (!chip) return;
    const number = Number(chip.dataset.number);
    if (picked.has(number)) picked.delete(number);
    else picked.add(number);
    chip.classList.toggle("is-picked", picked.has(number));
    telegram?.HapticFeedback?.selectionChanged?.();
    updateSummary();
  });
  pickedInput.addEventListener("input", updateSummary);
  restInput.addEventListener("input", updateSummary);
  updateSummary();

  // UZBMB matematika spetsifikatsiyasi (45 savol): geometriya — 24–30, 33–35, 41–44
  sheet.querySelector("#sections-preset")?.addEventListener("click", () => {
    const geometry = [24, 25, 26, 27, 28, 29, 30, 33, 34, 35, 41, 42, 43, 44];
    picked.clear();
    geometry.filter((number) => numbers.includes(number)).forEach((number) => picked.add(number));
    chips().forEach((chip) => chip.classList.toggle("is-picked", picked.has(Number(chip.dataset.number))));
    pickedInput.value = "Geometriya";
    restInput.value = "Algebra";
    updateSummary();
  });

  sheet.querySelector("#sections-save").addEventListener(
    "click",
    guarded(async () => {
      if (!picked.size) throw new Error(allowClear ? "Kamida bitta savolni belgilang (yoki «Bo'limsiz» ni bosing)" : "Kamida bitta savolni belgilang");
      const pickedLabel = pickedInput.value.trim() || "Geometriya";
      const restLabel = restInput.value.trim() || "Algebra";
      if (pickedLabel.toLowerCase() === restLabel.toLowerCase()) throw new Error("Ikki bo'lim nomi har xil bo'lsin");
      const rest = numbers.filter((number) => !picked.has(number));
      const pickedList = numbers.filter((number) => picked.has(number));
      const lines = [];
      if (rest.length) lines.push(`${restLabel}: ${compressNumbers(rest)}`);
      lines.push(`${pickedLabel}: ${compressNumbers(pickedList)}`);
      await onSave({ text: lines.join("\n"), picked: pickedList, pickedName: pickedLabel, restName: restLabel });
      close();
    })
  );
  sheet.querySelector("#sections-clear")?.addEventListener(
    "click",
    guarded(async () => {
      await onSave({ text: "", picked: [], pickedName: pickedInput.value.trim(), restName: restInput.value.trim() });
      close();
    })
  );
}

// Natijalar sahifasidan — saqlangan testning bo'limlari
function openSectionsSheet(testId, results) {
  const saved = results.sections || [];
  openNumberPicker({
    numbers: results.question_numbers || [],
    picked: saved.length >= 2 ? saved[1].numbers : [],
    pickedName: saved.length >= 2 ? saved[1].name : "Geometriya",
    restName: saved.length >= 2 ? saved[0].name : "Algebra",
    onSave: async ({ text }) => {
      await api(`/api/teacher/tests/${testId}/sections`, { method: "PUT", body: { text } });
      showToast(text ? "✅ Bo'limlar saqlandi" : "Bo'limlar olib tashlandi");
      setTimeout(() => renderResultsTab(testId), 0);
    },
  });
}

function renderResultsTestList(tests) {
  const cards = tests
    .map(
      (test) => `
        <div class="card card-entrance" data-results-test-id="${test.id}">
          <div class="card-body" style="width:100%">
            <div class="row-between" style="align-items:flex-start">
              <div>
                <h3 style="margin-bottom:6px">${escapeHtml(test.title)} ${testBadges(test)}</h3>
                <p style="margin:0"><b>${escapeHtml(test.subject)}</b> • Kod: <b>${escapeHtml(test.code)}</b></p>
                <p class="subtitle" style="margin:4px 0 0">👥 ${test.students} ta qatnashchi • ${formatDateTime(test.created_at)}</p>
              </div>
              <span style="color:var(--color-text-muted);font-size:22px">›</span>
            </div>
          </div>
        </div>`
    )
    .join("");

  showPage(`
    ${pageHeader("Natijalar")}
    <p class="subtitle">Qaysi testning natijalarini ko'rmoqchisiz?</p>
    ${cards}
  `);

  wireTeacherChrome("results");

  document.querySelectorAll("[data-results-test-id]").forEach((card) => {
    card.addEventListener("click", guarded(() => renderResultsTab(Number(card.dataset.resultsTestId))));
  });
}

async function renderTestResults(testId) {
  const results = await api(`/api/teacher/tests/${testId}/results`);

  const tableRow = (row) => `
        <tr class="result-row">
          <td>${row.rank}</td><td>${escapeHtml(row.name)}</td>
          <td>${row.correct}</td><td>${row.wrong}</td><td>${row.percent}%</td>
          ${results.uses_rasch ? `<td><b>${row.ball.toFixed(2)}</b></td>` : ""}<td><b>${escapeHtml(row.grade)}</b></td>
        </tr>`;

  showPage(`
    <button class="back-button" data-action="test">←</button>
    <h2>${escapeHtml(results.title)}</h2>
    <div class="card row-between" style="cursor:default" id="price-card">
      <span>💰 Narxi: <b id="price-display">${results.price ? formatNumber(results.price) + " so'm" : "Bepul"}</b></span>
      <button class="btn btn-ghost" id="edit-price-button" style="width:auto;padding:8px 16px">✏️ Tahrirlash</button>
    </div>

    <div class="stats-grid">
      <div class="stat-tile"><span class="stat-value">${results.summary.participants}</span>Qatnashchilar</div>
      ${
        results.uses_rasch
          ? `<div class="stat-tile"><span class="stat-value">${results.summary.avg_ball.toFixed(2)}</span>O'rtacha ball</div>
             <div class="stat-tile"><span class="stat-value">${results.summary.max_ball.toFixed(2)}</span>Eng yuqori ball</div>
             <div class="stat-tile"><span class="stat-value">${results.summary.top_count} ta</span>A+ va A</div>`
          : `<div class="stat-tile"><span class="stat-value">${results.summary.avg_percent}%</span>O'rtacha foiz</div>
             <div class="stat-tile"><span class="stat-value">${results.summary.max_percent}%</span>Eng yuqori</div>
             <div class="stat-tile"><span class="stat-value">${results.summary.top_count} ta</span>A'lo (5)</div>`
      }
    </div>
    <button class="btn" id="pdf-button">📄 PDF HISOBOT</button>
    <h3>Natijalar</h3>
    <input class="input" id="filter-input" placeholder="🔍 O'quvchini qidirish..." />
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>O'rin</th><th>O'quvchi</th><th>✅</th><th>❌</th><th>%</th>${results.uses_rasch ? "<th>Ball</th><th>Daraja</th>" : "<th>Baho</th>"}</tr></thead>
        <tbody id="results-table-body"></tbody>
      </table>
    </div>
    <button class="btn btn-ghost" id="question-stats-button" style="margin-top:14px">📊 Savol statistikasi ›</button>
  `);

  document.querySelector('[data-action="test"]').addEventListener("click", guarded(() => renderTestDetail(testId)));
  document.getElementById("pdf-button").addEventListener("click", guarded(() => downloadOrSendPdf(testId)));

  document.getElementById("edit-price-button").addEventListener("click", () => showPriceEditor(testId, results.price));
  document.getElementById("question-stats-button").addEventListener(
    "click",
    guarded(() => renderQuestionStatsPage(testId, results.mode, () => renderTestResults(testId)))
  );
  mountPagedList({
    container: document.getElementById("results-table-body"),
    items: results.rows,
    renderItem: tableRow,
    filterInput: document.getElementById("filter-input"),
    matches: (row, query) => row.name.toLowerCase().includes(query),
  });
}

function showPriceEditor(testId, currentPrice) {
  document.getElementById("price-card").innerHTML = `
    <input class="input" id="new-price-input" type="number" min="0" step="500" value="${currentPrice}" placeholder="Yangi narx (so'm, 0 = bepul)" style="margin:0" />
    <button class="btn" id="save-price-button" style="width:auto;padding:10px 16px">💾 Saqlash</button>
  `;
  document.getElementById("save-price-button").addEventListener("click", guarded(() => saveNewPrice(testId)));
}

async function saveNewPrice(testId) {
  const newPrice = Number(document.getElementById("new-price-input").value) || 0;
  await api(`/api/teacher/tests/${testId}/price`, { method: "PATCH", body: { price: newPrice } });
  showToast("✅ Narx yangilandi");
  await renderTestResults(testId);
}

async function downloadOrSendPdf(testId) {
  await deliverPdf(`/api/teacher/tests/${testId}/pdf`, "hisobot.pdf", "📄 PDF Telegram chatga yuborildi");
}

async function deliverPdf(basePath, fileName, sentMessage) {
  // "/.../answer-sheet?answers=true" ko'rinishida bo'lsa, "/send" manzil
  // qismidan keyin, savol belgisidan oldin turishi kerak.
  const [path, query] = basePath.split("?");
  const querySuffix = query ? `?${query}` : "";

  // Telegram ichida fayl yuklab olish cheklangan — shuning uchun PDF bot
  // orqali chatga yuboriladi. Oddiy brauzerda esa odatdagidek yuklab olinadi.
  if (telegram?.initData) {
    await api(`${path}/send${querySuffix}`, { method: "POST", body: {} });
    showToast(sentMessage);
    return;
  }

  const response = await fetch(basePath, { headers: { "X-Dev-User": DEV_USER_ID } });
  if (!response.ok) throw new Error("PDF yuklab bo'lmadi");

  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = fileName;
  link.click();
}

/* ---------- Ishga tushirish ---------- */

/*
  Guruhda ulashilgan maktab taklifidan kelgan o'qituvchi: bot Mini App'ni
  "?school_code=KOD" bilan ochadi — qo'shilish formasi kod bilan tayyor turadi.
  Allaqachon maktabga a'zo bo'lsa, o'z maktabi ochiladi.
*/
async function openPendingSchoolInvite() {
  const params = new URLSearchParams(window.location.search);

  // Admin yuborgan shaxsiy havola (?invite=TOKEN) — kodsiz qo'shilish
  const inviteToken = (params.get("invite") || "").trim();
  if (/^[A-Za-z0-9]{6,40}$/.test(inviteToken)) {
    params.delete("invite");
    const rest = params.toString();
    history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
    try {
      const { school } = await api("/api/schools/me");
      if (school) {
        showToast("Siz allaqachon maktabga a'zosiz");
        renderMySchool(school);
      } else {
        await renderInviteJoin(inviteToken);
      }
    } catch (error) {
      showToast(error.message || "Taklifni ochib bo'lmadi");
    }
    return;
  }

  const code = (params.get("school_code") || "").trim().toUpperCase();
  if (!/^[A-Z0-9]{4,20}$/.test(code)) return;

  // Bir martalik — sahifa yangilansa qayta ochilmasin
  params.delete("school_code");
  const query = params.toString();
  history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : "") + window.location.hash);

  try {
    const { school } = await api("/api/schools/me");
    if (school) renderMySchool(school);
    else await renderJoinSchool({ prefillCode: code, onBack: renderPrivateSchoolStart });
  } catch (error) {
    showToast(error.message || "Taklifni ochib bo'lmadi");
  }
}

/*
  Kirishda adminlar kanaliga (@sukhrobmath) obuna tekshiriladi. Obuna
  bo'lmagan foydalanuvchi ilovadan foydalana olmaydi — obuna ekrani chiqadi.
  Tekshirib bo'lmasa (internet) — to'smaydi, testlar baribir alohida tekshiriladi.
*/
async function checkChannelGate() {
  let missing = [];
  try {
    ({ missing } = await api("/api/me/subscription"));
  } catch (error) {
    return true;
  }
  if (!missing.length) return true;
  renderChannelGate(missing);
  return false;
}

function renderChannelGate(missing) {
  showPage(`
    <div class="result-number">📢</div>
    <h2 style="text-align:center">Avval kanalimizga obuna bo'ling</h2>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <p style="margin:0">Botdan foydalanish uchun <b>${missing.map(escapeHtml).join(", ")}</b> kanaliga obuna bo'ling, so'ng «Obuna bo'ldim» tugmasini bosing.</p>
      </div>
    </div>
    ${subscribeButtonsHtml(missing)}
    <button class="btn btn-ghost" id="gate-recheck" style="margin-top:12px">✅ Obuna bo'ldim</button>
  `);
  wireSubscribeButtons();
  document.getElementById("gate-recheck").addEventListener(
    "click",
    guarded(async () => {
      const { missing: still } = await api("/api/me/subscription");
      if (still.length) {
        showToast("Hali obuna ko'rinmayapti — bir oz kutib qayta urining");
        return;
      }
      showToast("✅ Rahmat! Obuna tasdiqlandi");
      await loadHome();
      await openPendingSchoolInvite();
    })
  );
}

loadHome()
  .then(() => {
    // Bo'limlar ma'lumoti darhol fonda yuklanadi — foydalanuvchi bosguncha tayyor tursin
    prefetchSchoolData();
    prefetchSectionData();
    return checkChannelGate();
  })
  .then((allowed) => allowed && openPendingSchoolInvite());
