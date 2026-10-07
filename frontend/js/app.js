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

async function api(path, { method = "GET", body, keepalive = false } = {}) {
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

function localTimeToUtcIso(value) {
  // <input type="datetime-local"> mahalliy vaqtni beradi — uni backend
  // kutayotgan UTC ko'rinishiga o'tkazamiz.
  return value ? new Date(value).toISOString().slice(0, 19) : null;
}

function showPage(html) {
  // Menyu avval yashiriladi — kerak bo'lsa, sahifa o'zi qaytadan ko'rsatadi.
  // Ikkalasi bitta qadamda bo'lgani uchun ekranda miltillash sezilmaydi.
  hideBottomNav();
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

async function copyToClipboard(text) {
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
  showToast(`📋 Nusxalandi: ${text}`);
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
  return new Promise((resolve) => {
    if (telegram?.showConfirm) {
      telegram.showConfirm(message, (confirmed) => resolve(Boolean(confirmed)));
    } else {
      resolve(window.confirm(message));
    }
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
  const host = document.getElementById("bottom-nav-host");
  if (host) host.hidden = true;
}

function pageHeader(title) {
  // Chapdagi "←" bosh sahifaga — o'quvchi/o'qituvchi tanlash ekraniga qaytaradi
  return `
    <div class="page-header">
      <button class="back-button" type="button" data-action="back" style="margin:0">←</button>
      <h2 style="margin:0;flex:1">${escapeHtml(title)}</h2>
    </div>`;
}

function wireTeacherChrome(activeTab) {
  document.querySelector('[data-action="back"]')?.addEventListener("click", loadHome);
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
      : `<span class="badge badge-green">Ochiq</span>`;

  return `${enrollment}${status}`;
}

function guarded(handler) {
  return async (...args) => {
    try {
      await handler(...args);
    } catch (error) {
      showToast(error.message || "Xatolik yuz berdi");
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
};

function roleCard(action, icon, title, description) {
  return `
    <div class="card card-entrance role-card" data-action="${action}">
      <div class="card-icon">${icon}</div>
      <div class="card-body"><h3>${title}</h3><p>${description}</p></div>
    </div>`;
}

function renderHome(userName, backendOk) {
  const greeting = userName ? `Xush kelibsiz,<br>${escapeHtml(userName)}` : "Test Platformasiga<br>xush kelibsiz";

  // Hammasi joyida bo'lganda hech narsa ko'rsatilmaydi — faqat haqiqiy xatoda ogohlantirish chiqadi.
  const statusHtml = backendOk ? "" : `<p class="status">Backend bilan aloqa yo'q ❌</p>`;

  showPage(`
    <h1 class="fade-up">${greeting}</h1>
    <p class="subtitle fade-up">Bilimingizni sinang va natijangizni kuzating.</p>

    ${roleCard("student", ROLE_ICONS.student, "O'QUVCHI", "Testlarni topish va ishlash")}
    ${roleCard("teacher", ROLE_ICONS.teacher, "O'QITUVCHI", "Test yaratish va natijalarni boshqarish")}
    ${isAdmin ? roleCard("admin", ROLE_ICONS.admin, "ADMIN", "Umumiy statistika va boshqaruv") : ""}

    ${statusHtml}
  `);

  document.querySelector('[data-action="student"]').addEventListener(
    "click",
    guarded(async () => {
      await renderStudentHome();
    })
  );
  document.querySelector('[data-action="teacher"]').addEventListener(
    "click",
    guarded(async () => {
      await enterTeacherArea();
    })
  );
  document.querySelector('[data-action="admin"]')?.addEventListener(
    "click",
    guarded(async () => {
      await renderAdminDashboard();
    })
  );
}

async function loadHome() {
  renderSkeleton();
  try {
    const me = await api("/api/me");
    isAdmin = Boolean(me.is_admin);
    isTeacher = Boolean(me.is_teacher);
    renderHome(me.full_name || null, true);
  } catch (error) {
    isAdmin = false;
    isTeacher = false;
    renderHome(null, false);
  }
}

async function renderAdminDashboard() {
  const [stats, topStudents, withdrawalRequests, teachers, cardTopUps] = await Promise.all([
    api("/api/admin/stats"),
    api("/api/admin/top-students"),
    api("/api/admin/withdrawal-requests"),
    api("/api/admin/teachers"),
    api("/api/admin/card-topups"),
  ]);

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
        await api(`/api/admin/withdrawal-requests/${requestId}/approve`, { method: "POST", body: {} });
        showToast("✅ To'landi deb belgilandi");
        await renderAdminDashboard();
      })
    );
    card.querySelector('[data-action="reject"]').addEventListener(
      "click",
      guarded(async () => {
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

async function renderStudentHome() {
  const [me, attempts] = await Promise.all([api("/api/me"), api("/api/me/attempts")]);

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
          <div class="card row-between" style="cursor:default">
            <span>${escapeHtml(item.title)}</span>
            <b>${item.percent}%</b>
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

  document.querySelector('[data-action="back"]')?.addEventListener("click", loadHome);
  document.getElementById("find-test-button").addEventListener("click", renderTestCodeSearch);
  document.getElementById("edit-name-button").addEventListener(
    "click",
    guarded(() => renderEditName(me, renderStudentHome))
  );
  document.querySelectorAll("[data-resume-code]").forEach((card) => {
    card.addEventListener("click", guarded(() => startTest(card.dataset.resumeCode)));
  });
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
      await api("/api/me/name", { method: "POST", body: { first_name, last_name } });
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
      : // To'lov Payme sahifasida amalga oshadi va tasdiq serverga webhook
        // orqali keladi — shuning uchun o'quvchi qaytib kelib holatni
        // yangilashi uchun alohida tugma turadi.
        `<button class="btn" id="pay-button">💳 ${formatNumber(test.price)} so'm to'lash</button>
         <button class="btn btn-ghost" id="recheck-payment-button">🔄 To'lovni tekshirish</button>`;

  document.getElementById("search-result").innerHTML = `
    <div class="card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <h3>📚 ${escapeHtml(test.title)}</h3>
        <p>📝 ${test.count} ta savol</p>
        <p>⏱️ ${test.duration} daqiqa</p>
        <p>💰 ${priceLabel}</p>
        ${
          test.required_channel
            ? `<p>📢 Kanalga obuna shart: <b>${escapeHtml(test.required_channel)}</b></p>
               <button class="btn btn-ghost" id="subscribe-button">Kanalga obuna bo'lish</button>`
            : ""
        }
        ${blockedMessage ? `<p style="color:var(--color-primary);font-weight:600">${blockedMessage}</p>` : ""}
        ${actionButton}
        <button class="btn btn-ghost" id="rating-button">🏆 Reyting</button>
      </div>
    </div>
  `;

  document.getElementById("start-test-button")?.addEventListener(
    "click",
    guarded(async () => {
      try {
        await startTest(test.code);
      } catch (error) {
        // Obunaga oid xatolarni alohida, tushunarli ekranda ko'rsatamiz —
        // bir lahzada o'tib ketadigan xabar bilan o'quvchi nima qilishni
        // bilmay qolardi.
        if (error.status === 451 || error.status === 409) {
          renderSubscriptionRequired(test, error.message, error.status === 409);
          return;
        }
        throw error;
      }
    })
  );

  document.getElementById("pay-button")?.addEventListener(
    "click",
    guarded(async () => {
      const payment = await api(`/api/tests/${test.code}/pay`, { method: "POST", body: {} });

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
  );

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
  document.getElementById("subscribe-button")?.addEventListener("click", () => {
    openExternalLink(`https://t.me/${test.required_channel.replace("@", "")}`);
  });
  document.getElementById("rating-button").addEventListener("click", guarded(() => renderRating(test.code)));
}

function renderSubscriptionRequired(test, message, isMisconfigured) {
  /*
    Majburiy obuna ekrani.

    Ikki xil holat bor va ularni aralashtirmaslik kerak:
    - o'quvchi hali obuna bo'lmagan (451) — o'zi hal qila oladi
    - bot kanalda admin emas (409) — buni faqat O'QITUVCHI hal qiladi
  */
  const channel = test.required_channel || "";

  showPage(`
    <button class="back-button" data-action="back">←</button>
    <div class="result-number">${isMisconfigured ? "⚠️" : "📢"}</div>
    <h2 style="text-align:center">${isMisconfigured ? "Obuna tekshirilmadi" : "Avval kanalga obuna bo'ling"}</h2>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <p>${escapeHtml(message)}</p>
      </div>
    </div>
    ${
      isMisconfigured
        ? ""
        : `<button class="btn" id="go-subscribe">📢 ${escapeHtml(channel)} — obuna bo'lish</button>
           <button class="btn btn-ghost" id="recheck-subscribe">✅ Obuna bo'ldim, tekshirish</button>`
    }
  `);

  document.querySelector('[data-action="back"]').addEventListener("click", guarded(() => renderTestFound(test)));

  document.getElementById("go-subscribe")?.addEventListener("click", () => {
    openExternalLink(`https://t.me/${channel.replace("@", "")}`);
  });

  document.getElementById("recheck-subscribe")?.addEventListener(
    "click",
    guarded(async () => {
      try {
        await startTest(test.code);
      } catch (error) {
        if (error.status === 451 || error.status === 409) {
          showToast("Hali obuna ko'rinmayapti — bir oz kutib qayta urining");
          return;
        }
        throw error;
      }
    })
  );
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

function renderKeyQuizQuestion() {
  const question = quizState.questions[quizState.index];
  const total = quizState.questions.length;
  const isLast = quizState.index === total - 1;
  const progressPercent = Math.round(((quizState.index + 1) / total) * 100);
  const isText = question.question_type === "text";

  // Yozma savol a va b qismlardan iborat bo'lishi mumkin — ular bitta
  // savol sahifasida, ketma-ket ko'rsatiladi.
  const parts = question.parts || [{ ref: question.number, label: "" }];

  const answerAreaHtml = parts
    .map((part) => {
      const saved = quizState.answers[part.ref] || "";
      const partTitle = part.label ? `<b class="part-letter">${part.label})</b>` : "";

      if (isText) {
        return `<div class="quiz-part">${partTitle}
          ${mathFieldHtml(saved, `data-ref="${part.ref}" placeholder="Javobingizni yozing"`)}
        </div>`;
      }

      return `<div class="quiz-part">${partTitle}
        <div class="options-grid" data-ref="${part.ref}">
          ${Array.from({ length: question.option_count }, (_, index) => {
            const letter = String.fromCharCode(65 + index);
            return `<div class="option ${saved === letter ? "selected" : ""}" data-letter="${letter}"
                         style="text-align:center;margin:0"><b>${letter}</b></div>`;
          }).join("")}
        </div>
      </div>`;
    })
    .join("");

  const hintText = isText
    ? parts.length > 1
      ? "Har bir qism uchun javobingizni yozing"
      : "Javobingizni yozib kiriting"
    : "To'g'ri javobni tanlang";

  showPage(`
    <div class="row-between">
      <b>${question.number}-savol / ${total}</b>
      <b id="quiz-timer"></b>
    </div>
    <div class="progress-track"><div class="progress-fill" style="width:${progressPercent}%"></div></div>
    <p class="subtitle">${hintText}</p>
    ${answerAreaHtml}
    ${isText ? `<p class="subtitle answer-hint">${answerInputHint()}</p>` : ""}
    <button class="btn" id="next-button" style="margin-top:16px">${isLast ? "🏁 TESTNI YAKUNLASH" : "Keyingi →"}</button>
  `);

  if (isText) {
    setupMathFields(appElement, (field, value) => {
      quizState.answers[field.dataset.ref] = value;
      scheduleAnswerSave();
    });
  } else {
    document.querySelectorAll(".options-grid").forEach((grid) => {
      grid.querySelectorAll(".option").forEach((optionEl) => {
        optionEl.addEventListener("click", () => {
          quizState.answers[grid.dataset.ref] = optionEl.dataset.letter;
          grid.querySelectorAll(".option").forEach((el) => el.classList.remove("selected"));
          optionEl.classList.add("selected");
          scheduleAnswerSave();
        });
      });
    });
  }

  document.getElementById("next-button").addEventListener(
    "click",
    guarded(async () => {
      if (isLast) {
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
    renderQuizResult(result, quizState.code);
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

function renderQuizResult(result, code) {
  showPage(`
    <div class="result-number pop">🎉</div>
    <h2 style="text-align:center">TEST YAKUNLANDI!</h2>
    <div class="result-number" id="result-percent">0%</div>
    <h2 style="text-align:center">${result.total} tadan ${result.correct} tasini yechdingiz</h2>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <p>✅ To'g'ri javoblar: <b>${result.correct}</b></p>
        <p>❌ Noto'g'ri javoblar: <b>${result.wrong}</b></p>
        <p>⏱️ Sarflangan vaqt: <b>${formatDuration(result.seconds)}</b></p>
      </div>
    </div>
    <button class="btn" id="view-rating-button">🏆 Reytingni ko'rish</button>
    <button class="btn btn-ghost" id="home-button">🏠 Bosh sahifa</button>
  `);

  document.getElementById("view-rating-button").addEventListener("click", guarded(() => renderRating(code)));
  document.getElementById("home-button").addEventListener("click", loadHome);

  animateCountUp(document.getElementById("result-percent"), result.percent);
}

async function renderRating(code) {
  const rating = await api(`/api/tests/${code}/rating`);
  const medals = ["🥇", "🥈", "🥉"];

  const rows =
    rating
      .map(
        (row, index) => `
          <div class="card row-between" style="cursor:default">
            <span>${medals[index] || index + 1 + "."} ${escapeHtml(row.name)}</span>
            <b>${row.percent}%</b>
          </div>`
      )
      .join("") || "<p>Hali natijalar yo'q</p>";

  showPage(`
    <button class="back-button" data-action="student">←</button>
    <h2>🏆 REYTING</h2>
    ${rows}
  `);

  document.querySelector('[data-action="student"]').addEventListener("click", guarded(renderStudentHome));
}

/* ---------- 4) O'qituvchi oqimi ---------- */

async function enterTeacherArea() {
  // Serverdan qayta so'raymiz: agar internet uzilgan bo'lsa, "ro'yxatdan o'ting"
  // degan ekranni ko'rsatib yubormaslik uchun. Ro'yxatdan o'tgan o'qituvchidan
  // hech qachon qayta ism-familiya so'ralmaydi.
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

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  document.getElementById("submit-register-button").addEventListener("click", guarded(submitTeacherRegister));
}

async function submitTeacherRegister() {
  const firstName = document.getElementById("first-name-input").value.trim();
  const lastName = document.getElementById("last-name-input").value.trim();

  await api("/api/teacher/register", { method: "POST", body: { first_name: firstName, last_name: lastName } });
  isTeacher = true;
  await renderTeacherDashboard();
}

async function renderTeacherDashboard() {
  let me;
  let stats;
  let tests;

  try {
    [me, stats, tests] = await Promise.all([api("/api/me"), api("/api/teacher/stats"), api("/api/teacher/tests")]);
  } catch (error) {
    isTeacher = false;
    throw error;
  }

  const visibleTests = tests.filter((test) => (teacherTestsTab === "closed" ? test.status === "closed" : test.status !== "closed"));

  const testCards =
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
      <div class="card-body">
        <div class="row-between"><span>💰 Balansingiz</span><b>${formatNumber(stats.balance)} so'm</b></div>
        <p>Pullik testlardan tushgan daromad</p>
        <button class="btn btn-ghost" id="withdraw-button">💸 Pul yechish</button>
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
    ${testCards}
  `);

  wireTeacherChrome("tests");
  document.getElementById("edit-name-button").addEventListener(
    "click",
    guarded(() => renderEditName(me, renderTeacherDashboard))
  );
  document.getElementById("withdraw-button").addEventListener("click", guarded(renderWithdrawalForm));
  document.getElementById("buy-credit-button").addEventListener("click", guarded(renderCreditPurchaseForm));
  document.getElementById("new-test-button").addEventListener("click", () => renderNewTestForm("rash"));

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener(
      "click",
      guarded(async () => {
        teacherTestsTab = tab.dataset.tab;
        await renderTeacherDashboard();
      })
    );
  });

  document.querySelectorAll("[data-test-id]").forEach((card) => {
    card.addEventListener("click", guarded(() => renderTestDetail(Number(card.dataset.testId))));
  });

  document.querySelectorAll("[data-delete-test]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(async (event) => {
        event.stopPropagation(); // kartaning o'zi ochilib ketmasin
        const testId = Number(button.dataset.deleteTest);
        const test = tests.find((item) => item.id === testId);

        if (!(await askConfirm(`"${test.title}" testi va uning barcha natijalari o'chirilsinmi?`))) {
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

  const paymeSection = balanceInfo.payme_enabled
    ? `<button class="btn ${balanceInfo.card_enabled ? "btn-ghost" : ""}" id="pay-with-payme-button">🅿️ Payme orqali to'lash</button>`
    : "";

  const noMethods =
    !balanceInfo.card_enabled && !balanceInfo.payme_enabled
      ? `<div class="card" style="cursor:default"><div class="card-body">
           <p>To'lov usullari hali sozlanmagan. Kredit olish uchun admin bilan bog'laning.</p>
         </div></div>`
      : "";

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2>Kredit sotib olish</h2>
    <p class="subtitle">Joriy kredit: <b>${formatNumber(balanceInfo.credits)} ta</b>. Necha dona sotib olmoqchisiz?</p>
    <input class="input" id="credit-count-input" type="number" min="1" placeholder="Kredit miqdori" value="50" />
    <p class="subtitle">Jami narx: <b id="credit-price-preview">—</b></p>
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
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));

  const creditInput = document.getElementById("credit-count-input");
  const pricePreview = document.getElementById("credit-price-preview");
  const cardAmount = document.getElementById("card-amount");

  const updatePricePreview = guarded(async () => {
    const credits = Number(creditInput.value) || 0;
    if (credits < 1) {
      pricePreview.textContent = "—";
      if (cardAmount) cardAmount.textContent = "—";
      return;
    }
    const price = await api(`/api/teacher/credit-price?credits=${credits}`);
    const text = `${formatNumber(price.amount)} so'm`;
    pricePreview.textContent = text;
    // Kartaga aynan shu summa o'tkazilishi kerak — admin chekni shu bilan solishtiradi
    if (cardAmount) cardAmount.textContent = text;
  });

  creditInput.addEventListener("input", updatePricePreview);
  updatePricePreview();

  const readCredits = () => {
    const credits = Number(creditInput.value) || 0;
    if (credits < 1) throw new Error("Kredit sonini kiriting");
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
  document.getElementById("pay-with-payme-button")?.addEventListener(
    "click",
    guarded(async () => {
      const credits = readCredits();
      const purchase = await api("/api/teacher/credit-purchases", { method: "POST", body: { credits } });
      openExternalLink(purchase.checkout_url);
    })
  );
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
      <div class="notice" style="margin-top:8px">
        ⚠️ O'quvchi to'lovi hali to'lov tizimiga ulanmagan — hozircha o'quvchi pul
        to'lamasdan testni ocha oladi, balansingizga esa haqiqiy bo'lmagan pul yoziladi.
        Pullik testni ishlatishdan oldin menga ayting, Payme'ni ulab beraman.
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
    <div class="setting-card">
      <label class="setting-row">
        <input type="checkbox" id="field-require-channel" ${draft.channel ? "checked" : ""} />
        <span>
          <b>Telegram kanalga majburiy obuna</b>
          <p class="subtitle" style="margin:4px 0 0">
            Yoqilgan bo'lsa, o'quvchilar testni boshlashdan oldin kanalingizga obuna bo'lishi shart.
          </p>
        </span>
      </label>
      <div id="channel-input-wrapper" style="display:${draft.channel ? "block" : "none"};margin-top:10px">
        <input class="input" id="field-channel" placeholder="@kanalingiz" value="${escapeHtml(draft.channel || "")}" style="margin:0" />
        <p class="subtitle" style="margin:6px 0 0;color:var(--color-primary)">
          Eslatma: bot kanalingizda administrator bo'lishi kerak!
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
         data-option-overrides='${JSON.stringify(structure.options)}'></div>`;

  showPage(`
    ${header}
    ${questionsSection}
    ${channelSettings}
    <div class="sticky-footer">
      <button class="btn" id="save-test-button" style="margin:0">Testni saqlash</button>
    </div>
  `);

  wireTeacherChrome("create");

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

  document.getElementById("save-test-button").addEventListener("click", guarded(saveNewTest));
  document.getElementById("build-key-rows-button").addEventListener("click", () => buildKeyQuestionRows());

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

  // Savollar soni o'zgartirilganda belgilangan javoblar yo'qolmasin
  const previous = { ...(savedAnswers || {}) };
  container.querySelectorAll(".key-row").forEach((row) => {
    const variants = [...row.querySelectorAll(".math-input")].map(readMathValue);
    previous[row.dataset.label] = {
      answer: row.dataset.answer || "",
      variants: variants.length ? variants : undefined,
      options: resetOptions ? undefined : row.dataset.options,
    };
  });

  // 1) Variantli savollar — ixcham katakchalar panjarasi.
  //    Katakchani bosganda javob A -> B -> C -> D -> A tarzida almashadi.
  const cells = [];
  for (let number = 1; number <= testCount; number += 1) {
    const saved = previous[number] || {};
    const options = Number(saved.options) || Number(optionOverrides[number]) || defaultOptions;
    const answer = saved.answer || "A";

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
  showToast(`✅ Test yaratildi! Kodi: ${result.code}`);

  // Test yaratilgach, darhol shu testning sahifasi ochiladi — o'quvchi qo'shish,
  // javoblar varaqasini olish va yakunlash shu yerdan qilinadi.
  teacherTestsTab = "active";
  await renderTestDetail(result.id);
}

/* ---------- 4.1) Test sahifasi (yaratilgandan keyin ochiladi) ---------- */

async function renderTestDetail(testId) {
  const test = await api(`/api/teacher/tests/${testId}`);
  const isClosedTest = test.enrollment_type === "closed";
  const isFinished = test.status === "closed";

  const studentRows =
    test.students
      .map(
        (student) => `
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
          </div>`
      )
      .join("") || `<p class="subtitle">Hali o'quvchi yo'q</p>`;

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2 style="text-align:center">${escapeHtml(test.title)}</h2>
    <div class="row-between" style="margin-bottom:14px">
      <div>${testBadges(test)}</div>
      <span class="subtitle" style="margin:0">Kod: ${copyCodeChip(test.code)}</span>
    </div>
    ${
      test.required_channel
        ? `<p class="subtitle">📢 Majburiy obuna: <b>${escapeHtml(test.required_channel)}</b></p>`
        : ""
    }

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
    <div id="students-list">${studentRows}</div>
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));

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

  document.querySelectorAll("[data-detail]").forEach((element) => {
    element.addEventListener(
      "click",
      guarded(() => renderStudentDetail(testId, Number(element.dataset.detail)))
    );
  });

  document.querySelectorAll("[data-edit-student]").forEach((button) => {
    button.addEventListener(
      "click",
      guarded(() => renderStudentAnswersForm(testId, Number(button.dataset.editStudent)))
    );
  });

  document.querySelectorAll("[data-delete-student]").forEach((button) => {
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

  document.getElementById("student-filter-input").addEventListener("input", (event) => {
    const query = event.target.value.toLowerCase();
    document.querySelectorAll(".list-row").forEach((row) => {
      row.style.display = row.textContent.toLowerCase().includes(query) ? "" : "none";
    });
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
      <div class="stat-tile"><span class="stat-value">${formatDuration(detail.seconds)}</span>Vaqt</div>
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

  const testId = selectedId || tests[0].id;

  // Faqat natijalar so'raladi — ishtirokchilar soni va o'rtacha foiz shu
  // javobning o'zidan hisoblanadi, qo'shimcha so'rov kerak emas.
  const results = await api(`/api/teacher/tests/${testId}/results`);
  const percents = results.rows.map((row) => row.percent);
  const average = percents.length ? Math.round((percents.reduce((sum, p) => sum + p, 0) / percents.length) * 10) / 10 : 0;

  // Ball taqsimoti: nechta o'quvchi qaysi oraliqda
  const buckets = [
    { label: "<30", from: 0, to: 30 },
    { label: "30-45", from: 30, to: 45 },
    { label: "45-60", from: 45, to: 60 },
    { label: "60-75", from: 60, to: 75 },
    { label: "75+", from: 75, to: 101 },
  ].map((bucket) => ({
    ...bucket,
    count: results.rows.filter((row) => row.percent >= bucket.from && row.percent < bucket.to).length,
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

  const rows = results.rows
    .map(
      (row, index) => `
      <div class="result-line" data-detail-id="${row.attempt_id}">
        <span class="result-rank">${index + 1}</span>
        <span class="result-name">${escapeHtml(row.name)}</span>
        <span class="result-score">${row.closed_correct}/${row.closed_total}${
          row.written_total ? ` • ${row.written_correct}/${row.written_total}` : ""
        }</span>
        <span class="result-percent">${row.percent}%</span>
      </div>`
    )
    .join("") || `<p class="subtitle" style="padding:14px">Hali natija yo'q</p>`;

  showPage(`
    ${pageHeader("Natijalar")}
    <label class="field-label" for="results-test-select">Test</label>
    <select class="input" id="results-test-select">
      ${tests
        .map(
          (test) =>
            `<option value="${test.id}" ${test.id === testId ? "selected" : ""}>${escapeHtml(test.title)}</option>`
        )
        .join("")}
    </select>

    <div class="panel">
      <div class="stats-grid" style="margin:0 0 16px">
        <div class="stat-tile"><span class="stat-value">${results.rows.length}</span>Ishtirokchi</div>
        <div class="stat-tile"><span class="stat-value">${average}%</span>O'rtacha</div>
      </div>
      <p class="section-label" style="margin:0 0 10px">BALL TAQSIMOTI</p>
      <div class="chart">${chart}</div>
    </div>

    <div class="row-between" style="margin:18px 0 8px">
      <span class="section-label" style="margin:0">ISHTIROKCHILAR</span>
      <span class="subtitle" style="margin:0;font-size:12px">yopiq • yozma</span>
    </div>
    <div class="panel" style="padding:4px 0">${rows}</div>
  `);

  wireTeacherChrome("results");

  document.getElementById("results-test-select").addEventListener(
    "change",
    guarded((event) => renderResultsTab(Number(event.target.value)))
  );

  document.querySelectorAll("[data-detail-id]").forEach((line) => {
    line.addEventListener(
      "click",
      guarded(() => renderStudentDetail(testId, Number(line.dataset.detailId)))
    );
  });
}

async function renderTestResults(testId) {
  const [results, stats] = await Promise.all([
    api(`/api/teacher/tests/${testId}/results`),
    api(`/api/teacher/tests/${testId}/stats`),
  ]);

  const tableRows = results.rows
    .map(
      (row, index) => `
        <tr class="result-row">
          <td>${index + 1}</td><td>${escapeHtml(row.name)}</td>
          <td>${row.correct}</td><td>${row.wrong}</td><td>${row.percent}%</td>
        </tr>`
    )
    .join("");

  const questionBlocks = stats.questions
    .map(
      (q, index) => `
        <div class="card" style="cursor:default">
          <div class="card-body">
            <b>${index + 1}. ${escapeHtml(q.text)}</b>
            <p>Bu savolga ${q.total} urinishdan ${q.wrong} tasi noto'g'ri javob berdi</p>
          </div>
        </div>`
    )
    .join("");

  showPage(`
    <button class="back-button" data-action="test">←</button>
    <h2>${escapeHtml(results.title)}</h2>
    <div class="card row-between" style="cursor:default" id="price-card">
      <span>💰 Narxi: <b id="price-display">${results.price ? formatNumber(results.price) + " so'm" : "Bepul"}</b></span>
      <button class="btn btn-ghost" id="edit-price-button" style="width:auto;padding:8px 16px">✏️ Tahrirlash</button>
    </div>
    <div class="stats-grid">
      <div class="stat-tile"><span class="stat-value">${stats.total}</span>Qatnashchilar</div>
      <div class="stat-tile"><span class="stat-value">${stats.avg}%</span>O'rtacha</div>
      <div class="stat-tile"><span class="stat-value">${stats.max}%</span>Eng yuqori</div>
      <div class="stat-tile"><span class="stat-value">${stats.min}%</span>Eng past</div>
    </div>
    <p>⏱️ O'rtacha vaqt: <b>${formatDuration(stats.avg_seconds)}</b></p>
    <button class="btn" id="pdf-button">📄 PDF HISOBOT</button>
    <h3>Natijalar</h3>
    <input class="input" id="filter-input" placeholder="🔍 O'quvchini qidirish..." />
    <div style="overflow-x:auto">
      <table>
        <tr><th>№</th><th>O'quvchi</th><th>✅</th><th>❌</th><th>%</th></tr>
        ${tableRows}
      </table>
    </div>
    <h3>Savollar bo'yicha xatolar</h3>
    ${questionBlocks}
  `);

  document.querySelector('[data-action="test"]').addEventListener("click", guarded(() => renderTestDetail(testId)));
  document.getElementById("pdf-button").addEventListener("click", guarded(() => downloadOrSendPdf(testId)));
  document.getElementById("edit-price-button").addEventListener("click", () => showPriceEditor(testId, results.price));
  document.getElementById("filter-input").addEventListener("input", (event) => {
    const query = event.target.value.toLowerCase();
    document.querySelectorAll(".result-row").forEach((row) => {
      row.style.display = row.textContent.toLowerCase().includes(query) ? "" : "none";
    });
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

loadHome();
