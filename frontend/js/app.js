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

async function api(path, { method = "GET", body } = {}) {
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
  });

  if (!response.ok) {
    let message = "Xatolik yuz berdi";
    try {
      const errorData = await response.json();
      if (typeof errorData.detail === "string") message = errorData.detail;
    } catch (_) {
      /* javobda JSON bo'lmasligi mumkin — standart xabar qoladi */
    }
    throw new Error(message);
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

function formatMinutesSeconds(totalSeconds) {
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatDateTime(isoString) {
  // Backend vaqtni har doim UTC'da saqlaydi va mintaqasiz yuboradi
  // ("2026-10-06T10:35:00"), shuning uchun "Z" qo'shib, foydalanuvchining
  // mahalliy vaqtiga o'giramiz — aks holda sana 5 soat farq qilib ko'rinadi.
  const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(isoString);
  const date = new Date(hasTimezone ? isoString : `${isoString}Z`);

  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} - ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function localTimeToUtcIso(value) {
  // <input type="datetime-local"> mahalliy vaqtni beradi — uni backend
  // kutayotgan UTC ko'rinishiga o'tkazamiz.
  return value ? new Date(value).toISOString().slice(0, 19) : null;
}

function showPage(html) {
  appElement.innerHTML = `<div class="page">${html}</div>`;
  window.scrollTo(0, 0);
}

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2600);
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

function renderSkeleton() {
  showPage(`
    <div class="skeleton skeleton-line" style="width:65%;height:26px"></div>
    <div class="skeleton skeleton-line" style="width:45%;margin-bottom:24px"></div>
    <div class="skeleton skeleton-card"></div>
    <div class="skeleton skeleton-card"></div>
  `);
}

function renderHome(userName, backendOk) {
  const greeting = userName
    ? `Xush kelibsiz, ${escapeHtml(userName)} 👋`
    : "Test Platformasiga xush kelibsiz 👋";

  // Hammasi joyida bo'lganda hech narsa ko'rsatilmaydi — faqat haqiqiy xatoda ogohlantirish chiqadi.
  const statusHtml = backendOk ? "" : `<p class="status">Backend bilan aloqa yo'q ❌</p>`;

  showPage(`
    <h1 class="fade-up">${greeting}</h1>
    <p class="subtitle fade-up">Bilimingizni sinang va natijangizni kuzating.</p>

    <div class="card card-entrance" data-action="student">
      <div class="card-icon">👨‍🎓</div>
      <div class="card-body"><h3>O'QUVCHI</h3><p>Testlarni topish va ishlash</p></div>
    </div>

    <div class="card card-entrance" data-action="teacher">
      <div class="card-icon">👨‍🏫</div>
      <div class="card-body"><h3>O'QITUVCHI</h3><p>Test yaratish va natijalarni boshqarish</p></div>
    </div>

    ${isAdmin
      ? `<div class="card card-entrance" data-action="admin">
             <div class="card-icon">👑</div>
             <div class="card-body"><h3>ADMIN</h3><p>Umumiy statistika va boshqaruv</p></div>
           </div>`
      : ""
    }

    ${statusHtml}
  `);

  document.querySelector('[data-action="student"]').addEventListener("click", guarded(renderStudentHome));
  document.querySelector('[data-action="teacher"]').addEventListener("click", guarded(enterTeacherArea));
  document.querySelector('[data-action="admin"]')?.addEventListener("click", guarded(renderAdminDashboard));
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
  const [stats, topStudents, withdrawalRequests] = await Promise.all([
    api("/api/admin/stats"),
    api("/api/admin/top-students"),
    api("/api/admin/withdrawal-requests"),
  ]);

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
    <button class="back-button" data-action="home">←</button>
    <h2>👑 Admin</h2>
    <div class="stats-grid">
      <div class="stat-tile">👥<span class="stat-value">${stats.users}</span>Foydalanuvchilar</div>
      <div class="stat-tile">🧑‍🏫<span class="stat-value">${stats.teachers}</span>O'qituvchilar</div>
      <div class="stat-tile">📚<span class="stat-value">${stats.tests}</span>Testlar</div>
      <div class="stat-tile">✅<span class="stat-value">${stats.attempts}</span>Yakunlangan urinishlar</div>
    </div>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <h3>💰 Jami to'lovlar</h3>
        <p>${formatNumber(stats.revenue)} so'm (hozircha mock to'lovlar)</p>
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
    <h3>💸 Pul yechish so'rovlari</h3>
    ${withdrawalRows}
    <h3>🏆 Eng yaxshi o'quvchilar (barcha testlar bo'yicha)</h3>
    ${topRows}
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);

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
    <button class="back-button" data-action="home">←</button>
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

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
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
  } else if (test.start_time && now < new Date(test.start_time)) {
    blockedMessage = `⏳ Test hali boshlanmagan. Boshlanish vaqti: ${formatDateTime(test.start_time)}`;
  } else if (test.end_time && now > new Date(test.end_time)) {
    blockedMessage = "⌛ Test vaqti tugagan.";
  }

  const actionButton = blockedMessage
    ? `<button class="btn" disabled>▶️ TESTNI BOSHLASH</button>`
    : test.paid
      ? `<button class="btn" id="start-test-button">▶️ TESTNI BOSHLASH</button>`
      : `<button class="btn" id="pay-button">💳 ${formatNumber(test.price)} so'm to'lash</button>
       <button class="btn" disabled>▶️ TESTNI BOSHLASH</button>`;

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

  document.getElementById("start-test-button")?.addEventListener("click", guarded(() => startTest(test.code)));
  document.getElementById("pay-button")?.addEventListener(
    "click",
    guarded(async () => {
      await api(`/api/tests/${test.code}/pay`, { method: "POST", body: {} });
      showToast("✅ To'lov muvaffaqiyatli (test rejim)");
      renderTestFound(await api(`/api/tests/${test.code}`));
    })
  );
  document.getElementById("subscribe-button")?.addEventListener("click", () => {
    const link = `https://t.me/${test.required_channel.replace("@", "")}`;
    if (telegram?.openTelegramLink) {
      telegram.openTelegramLink(link);
    } else {
      window.open(link, "_blank");
    }
  });
  document.getElementById("rating-button").addEventListener("click", guarded(() => renderRating(test.code)));
}

async function startTest(code) {
  const data = await api(`/api/tests/${code}/start`, { method: "POST", body: {} });

  quizState = {
    attemptId: data.attempt_id,
    mode: data.mode,
    questions: data.questions,
    index: 0,
    answers: {},
    endsAt: Date.now() + data.remaining * 1000,
    code,
    finished: false,
  };

  renderQuizQuestion();
  clearInterval(quizTimerId);
  quizTimerId = setInterval(() => guarded(updateQuizTimer)(), 1000);
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
    });
  });

  document.getElementById("next-button").addEventListener(
    "click",
    guarded(async () => {
      if (isLast) {
        await finishQuiz();
      } else {
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
  const isMulti = question.question_type === "multi";
  const isText = question.question_type === "text";

  const selectedAnswer = quizState.answers[question.number] || "";
  const selectedLetters = new Set(selectedAnswer ? selectedAnswer.split(",") : []);

  const answerAreaHtml = isText
    ? `<input class="input" id="text-answer-input" placeholder="Javobingizni yozing" value="${escapeHtml(selectedAnswer)}" />`
    : `<div id="options-container" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(60px,1fr));gap:10px">
         ${Array.from({ length: question.option_count }, (_, index) => {
      const letter = String.fromCharCode(65 + index);
      const selected = selectedLetters.has(letter) ? "selected" : "";
      return `<div class="option ${selected}" data-letter="${letter}" style="text-align:center"><b>${letter}</b></div>`;
    }).join("")}
       </div>`;

  const hintText = isText ? "Javobingizni yozib kiriting" : isMulti ? "Bir nechta to'g'ri javobni tanlang" : "To'g'ri javobni tanlang";

  showPage(`
    <div class="row-between">
      <b>${question.number}-savol / ${total}</b>
      <b id="quiz-timer"></b>
    </div>
    <div class="progress-track"><div class="progress-fill" style="width:${progressPercent}%"></div></div>
    <p class="subtitle">${hintText}</p>
    ${answerAreaHtml}
    <button class="btn" id="next-button" style="margin-top:16px">${isLast ? "🏁 TESTNI YAKUNLASH" : "Keyingi →"}</button>
  `);

  if (isText) {
    document.getElementById("text-answer-input").addEventListener("input", (event) => {
      quizState.answers[question.number] = event.target.value;
    });
  } else {
    document.querySelectorAll("#options-container .option").forEach((optionEl) => {
      optionEl.addEventListener("click", () => {
        const letter = optionEl.dataset.letter;

        if (isMulti) {
          if (selectedLetters.has(letter)) {
            selectedLetters.delete(letter);
          } else {
            selectedLetters.add(letter);
          }
          quizState.answers[question.number] = [...selectedLetters].sort().join(",");
          optionEl.classList.toggle("selected");
        } else {
          quizState.answers[question.number] = letter;
          document.querySelectorAll("#options-container .option").forEach((el) => el.classList.remove("selected"));
          optionEl.classList.add("selected");
        }
      });
    });
  }

  document.getElementById("next-button").addEventListener(
    "click",
    guarded(async () => {
      if (isLast) {
        await finishQuiz();
      } else {
        quizState.index += 1;
        renderQuizQuestion();
      }
    })
  );

  updateQuizTimer();
}

async function updateQuizTimer() {
  const timerEl = document.getElementById("quiz-timer");
  const secondsLeft = Math.max(0, Math.round((quizState.endsAt - Date.now()) / 1000));
  if (timerEl) timerEl.textContent = "⏱️ " + formatMinutesSeconds(secondsLeft);
  if (secondsLeft <= 0 && !quizState.finished) {
    await finishQuiz();
  }
}

async function finishQuiz() {
  if (quizState.finished) return;
  quizState.finished = true;
  clearInterval(quizTimerId);

  const result = await api(`/api/attempts/${quizState.attemptId}/finish`, {
    method: "POST",
    body: { answers: quizState.answers },
  });

  renderQuizResult(result, quizState.code);
}

function renderQuizResult(result, code) {
  showPage(`
    <div class="result-number pop">🎉</div>
    <h2 style="text-align:center">TEST YAKUNLANDI!</h2>
    <div class="result-number" id="result-percent">0%</div>
    <h2 style="text-align:center">${result.correct} / ${result.total}</h2>
    <div class="card" style="cursor:default">
      <div class="card-body">
        <p>✅ To'g'ri javoblar: <b>${result.correct}</b></p>
        <p>❌ Noto'g'ri javoblar: <b>${result.wrong}</b></p>
        <p>⏱️ Sarflangan vaqt: <b>${formatMinutesSeconds(result.seconds)}</b></p>
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
                  <p class="subtitle" style="margin:0">Test kodi: ${test.code}</p>
                  <p class="subtitle" style="margin:0">Yaratilgan vaqti: ${formatDateTime(test.created_at)}</p>
                </div>
                <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
                  <button class="icon-button danger" data-delete-test="${test.id}" title="O'chirish">🗑</button>
                  <span style="color:var(--color-text-muted);font-size:22px">›</span>
                </div>
              </div>
            </div>
          </div>`
      )
      .join("") ||
    `<p class="subtitle">${teacherTestsTab === "closed" ? "Yopilgan testlar yo'q" : "Hali testlar yo'q"}</p>`;

  showPage(`
    <button class="back-button" data-action="home">←</button>
    <div class="card" style="cursor:default">
      <div class="card-body" style="width:100%">
        <div class="row-between">
          <h3>${escapeHtml(me.full_name)} <span style="color:var(--color-primary);font-weight:400">o'qituvchi</span></h3>
          <button class="btn btn-ghost" id="edit-name-button" style="width:auto;padding:6px 10px">✏️</button>
        </div>
        <p>Telegram ID: <b>${me.tg_id}</b></p>
      </div>
    </div>
    <h2>Dashboard</h2>
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
        <p>Har bir o'quvchi natijasi uchun 1 kredit sarflanadi (bepul testlar uchun)</p>
        <button class="btn btn-ghost" id="buy-credit-button">🛒 Kredit sotib olish</button>
      </div>
    </div>
    <div class="row-between" style="margin:22px 0 12px">
      <h2 style="margin:0">Testlar</h2>
      <button class="btn" id="new-test-button" style="width:auto;padding:10px 16px">➕ Test qo'shish</button>
    </div>
    <div class="tabs">
      <button class="tab ${teacherTestsTab === "active" ? "active" : ""}" data-tab="active">Jarayondagi testlar</button>
      <button class="tab ${teacherTestsTab === "closed" ? "active" : ""}" data-tab="closed">Yopilgan testlar</button>
    </div>
    ${testCards}
    <p class="status">✅</p>
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  document.getElementById("edit-name-button").addEventListener(
    "click",
    guarded(() => renderEditName(me, renderTeacherDashboard))
  );
  document.getElementById("withdraw-button").addEventListener("click", guarded(renderWithdrawalForm));
  document.getElementById("buy-credit-button").addEventListener("click", guarded(renderCreditPurchaseForm));
  document.getElementById("new-test-button").addEventListener("click", () => renderNewTestForm("key"));

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

  const statusLabels = { pending: "⏳ Kutilmoqda", paid: "✅ To'landi", cancelled: "❌ Bekor qilindi" };
  const historyRows =
    history
      .map(
        (purchase) => `
          <div class="card row-between" style="cursor:default">
            <span>${purchase.credits} ta kredit (${formatNumber(purchase.amount)} so'm)</span>
            <b>${statusLabels[purchase.status]}</b>
          </div>`
      )
      .join("") || "<p>Hali buyurtmalar yo'q</p>";

  showPage(`
    <button class="back-button" data-action="dashboard">←</button>
    <h2>Kredit sotib olish</h2>
    <p class="subtitle">Necha dona kredit sotib olmoqchisiz?</p>
    <input class="input" id="credit-count-input" type="number" min="1" placeholder="Kredit miqdori" value="10" />
    <div class="card" style="cursor:default">
      <div class="card-body">
        <h3>Narxlash tizimi</h3>
        ${tierRows}
      </div>
    </div>
    <p class="subtitle">Jami narx: <b id="credit-price-preview">—</b></p>
    <button class="btn" id="pay-with-payme-button">💳 Payme orqali to'lash</button>
    <h3>Buyurtmalar tarixi</h3>
    ${historyRows}
  `);

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));

  const creditInput = document.getElementById("credit-count-input");
  const pricePreview = document.getElementById("credit-price-preview");

  const updatePricePreview = guarded(async () => {
    const credits = Number(creditInput.value) || 0;
    if (credits < 1) {
      pricePreview.textContent = "—";
      return;
    }
    const price = await api(`/api/teacher/credit-price?credits=${credits}`);
    pricePreview.textContent = `${formatNumber(price.amount)} so'm`;
  });

  creditInput.addEventListener("input", updatePricePreview);
  updatePricePreview();

  document.getElementById("pay-with-payme-button").addEventListener(
    "click",
    guarded(async () => {
      const credits = Number(creditInput.value) || 0;
      if (credits < 1) {
        showToast("Kredit sonini kiriting");
        return;
      }
      const purchase = await api("/api/teacher/credit-purchases", { method: "POST", body: { credits } });
      if (telegram?.openLink) {
        telegram.openLink(purchase.checkout_url);
      } else {
        window.open(purchase.checkout_url, "_blank");
      }
    })
  );
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

function renderNewTestForm(mode, draft = {}) {
  const selectOption = (value, label, selected) =>
    `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`;

  const subjectOptions = SUBJECTS.map((subject) => selectOption(subject, subject, draft.subject)).join("");

  const header = `
    <button class="back-button" data-action="dashboard">←</button>
    <h2>Test qo'shish</h2>

    <input class="input" id="field-title" placeholder="Test nomini kiriting" value="${escapeHtml(draft.title || "")}" />

    <label class="field-label" for="field-mode">Test turi</label>
    <select class="input" id="field-mode">
      ${selectOption("key", "Rash test (javoblar kaliti)", mode)}
      ${selectOption("full", "To'liq savolli test", mode)}
    </select>

    <label class="field-label" for="field-subject">Fanni tanlang</label>
    <select class="input" id="field-subject">
      ${subjectOptions}
    </select>

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

    <div class="row-between" style="gap:8px">
      <div style="flex:1">
        <label class="field-label" for="field-price">Narxi (so'm)</label>
        <input class="input" id="field-price" type="number" min="0" value="${draft.price ?? 0}" style="margin:0" />
      </div>
      <div style="flex:1">
        <label class="field-label" for="field-duration">Davomiyligi (daqiqa)</label>
        <input class="input" id="field-duration" type="number" min="1" value="${draft.duration ?? 30}" style="margin:0" />
      </div>
    </div>
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

  const questionsSection =
    mode === "key"
      ? `
        <h3 style="margin-top:22px">Savollar</h3>
        <p class="subtitle">
          Savol matni kerak emas — u kanalingizda joylashtirilgan bo'ladi.
          Sonini yozing, hamma savol birdan chiqadi.
        </p>
        <div class="row-between" style="gap:8px">
          <div style="flex:1">
            <label class="field-label" for="field-test-count">Test savollari</label>
            <input class="input" id="field-test-count" type="number" min="0" max="300" value="${draft.testCount ?? 30}" style="margin:0" />
          </div>
          <div style="flex:1">
            <label class="field-label" for="field-option-count">Variantlar</label>
            <input class="input" id="field-option-count" type="number" min="2" max="8" value="${draft.optionCount ?? 4}" style="margin:0" />
          </div>
          <div style="flex:1">
            <label class="field-label" for="field-text-count">Yozma</label>
            <input class="input" id="field-text-count" type="number" min="0" max="300" value="${draft.textCount ?? 0}" style="margin:0" />
          </div>
        </div>
        <button class="btn btn-ghost" id="build-key-rows-button" style="margin-top:10px">🔄 Savollarni yangilash</button>
        <p class="subtitle" style="margin:14px 0 6px">
          To'g'ri javobni bosing. Bir nechta harf tanlasangiz, savol bir nechta to'g'ri javobli bo'ladi.
          Har bir savolda variantlar sonini − va + bilan o'zgartirish mumkin.
        </p>
        <div id="key-questions-container"></div>`
      : `
        <h3 style="margin-top:22px">Savollar</h3>
        <p class="subtitle">Har bir savolning matni va variantlarini kiriting.</p>
        <div id="questions-container"></div>
        <button class="btn btn-ghost" id="add-question-button">➕ Savol qo'shish</button>`;

  showPage(`
    ${header}
    ${questionsSection}
    ${channelSettings}
    <div class="sticky-footer">
      <button class="btn" id="save-test-button" style="margin:0">Test yaratish</button>
    </div>
  `);

  // "Boshqa fan" tanlanmagan bo'lsa ham, avvalgi qiymat ro'yxatda bo'lmasligi mumkin
  const subjectSelect = document.getElementById("field-subject");
  if (draft.subject && !SUBJECTS.includes(draft.subject)) {
    subjectSelect.value = SUBJECTS[0];
  }

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));

  // Test turi o'zgartirilganda forma qaytadan chiziladi — lekin kiritilgan
  // ma'lumotlar yo'qolmasligi uchun ularni "draft" sifatida olib o'tamiz.
  document.getElementById("field-mode").addEventListener("change", (event) => {
    renderNewTestForm(event.target.value, collectTestFormDraft(mode));
  });

  const enrollmentToggle = document.getElementById("enrollment-toggle");
  const enrollmentOpenButton = document.getElementById("enrollment-open-button");
  const enrollmentClosedButton = document.getElementById("enrollment-closed-button");
  const enrollmentHint = document.getElementById("enrollment-hint");

  enrollmentOpenButton.addEventListener("click", () => {
    enrollmentToggle.dataset.value = "open";
    enrollmentOpenButton.classList.remove("btn-ghost");
    enrollmentClosedButton.classList.add("btn-ghost");
    enrollmentHint.textContent = "O'quvchilar test kodini kiritib o'zlari qo'shiladi";
  });
  enrollmentClosedButton.addEventListener("click", () => {
    enrollmentToggle.dataset.value = "closed";
    enrollmentClosedButton.classList.remove("btn-ghost");
    enrollmentOpenButton.classList.add("btn-ghost");
    enrollmentHint.textContent = "O'qituvchi o'quvchilarni o'zi qo'shib, javoblarini kiritadi";
  });

  const channelCheckbox = document.getElementById("field-require-channel");
  channelCheckbox.addEventListener("change", () => {
    document.getElementById("channel-input-wrapper").style.display = channelCheckbox.checked ? "block" : "none";
  });

  document.getElementById("save-test-button").addEventListener("click", guarded(() => saveNewTest(mode)));

  if (mode === "key") {
    document.getElementById("build-key-rows-button").addEventListener("click", buildKeyQuestionRows);
    buildKeyQuestionRows(draft.answers);
  } else {
    document.getElementById("add-question-button").addEventListener("click", addQuestionBlock);
    addQuestionBlock();
  }
}

function collectTestFormDraft(mode) {
  const answers = {};
  document.querySelectorAll("#key-questions-container .key-row").forEach((row) => {
    answers[row.dataset.number] = { answer: row.dataset.answer || "", options: row.dataset.options };
  });

  return {
    title: document.getElementById("field-title").value,
    subject: document.getElementById("field-subject").value,
    enrollment: document.getElementById("enrollment-toggle").dataset.value,
    startTime: document.getElementById("field-start-time").value,
    endTime: document.getElementById("field-end-time").value,
    price: document.getElementById("field-price").value,
    duration: document.getElementById("field-duration").value,
    channel: document.getElementById("field-require-channel").checked
      ? document.getElementById("field-channel").value
      : "",
    testCount: mode === "key" ? document.getElementById("field-test-count").value : undefined,
    optionCount: mode === "key" ? document.getElementById("field-option-count").value : undefined,
    textCount: mode === "key" ? document.getElementById("field-text-count").value : undefined,
    answers: mode === "key" ? answers : undefined,
  };
}

function addQuestionBlock() {
  const block = document.createElement("div");
  block.className = "question-block";
  block.innerHTML = `
    <textarea class="input" data-field="text" placeholder="Savol matni"></textarea>
    <input class="input" data-field="a" placeholder="A javob" />
    <input class="input" data-field="b" placeholder="B javob" />
    <input class="input" data-field="c" placeholder="C javob" />
    <input class="input" data-field="d" placeholder="D javob" />
    <select class="input" data-field="correct">
      <option value="A">To'g'ri javob: A</option>
      <option value="B">To'g'ri javob: B</option>
      <option value="C">To'g'ri javob: C</option>
      <option value="D">To'g'ri javob: D</option>
    </select>
    <button class="btn btn-ghost" type="button" data-action="remove-question">🗑 O'chirish</button>
  `;
  block.querySelector('[data-action="remove-question"]').addEventListener("click", () => block.remove());
  document.getElementById("questions-container").appendChild(block);
}

function buildKeyQuestionRows(savedAnswers) {
  const container = document.getElementById("key-questions-container");
  const testCount = Math.max(0, Number(document.getElementById("field-test-count").value) || 0);
  const defaultOptions = Math.min(8, Math.max(2, Number(document.getElementById("field-option-count").value) || 4));
  const textCount = Math.max(0, Number(document.getElementById("field-text-count").value) || 0);

  if (testCount + textCount === 0) {
    showToast("Kamida bitta savol bo'lsin");
    return;
  }

  // Savollar soni o'zgartirilganda (yoki test turi almashtirilganda),
  // allaqachon belgilangan javoblar yo'qolmasin.
  const previous = { ...(savedAnswers || {}) };
  container.querySelectorAll(".key-row").forEach((row) => {
    previous[row.dataset.number] = { answer: row.dataset.answer || "", options: row.dataset.options };
  });

  const rows = [];

  for (let number = 1; number <= testCount; number += 1) {
    const saved = previous[number] || {};
    const options = Number(saved.options) || defaultOptions;

    rows.push(`
      <div class="key-row" data-number="${number}" data-kind="letters" data-options="${options}"
           data-answer="${saved.answer || ""}">
        <div class="key-row-head">
          <b>${number}-savol</b>
          <div class="option-stepper">
            <button type="button" data-step="-1">−</button>
            <span class="option-count">${options}</span>
            <button type="button" data-step="1">+</button>
          </div>
        </div>
        <div class="letter-grid"></div>
      </div>`);
  }

  for (let index = 0; index < textCount; index += 1) {
    const number = testCount + index + 1;
    const saved = previous[number] || {};
    const answer = escapeHtml(saved.answer || "");

    rows.push(`
      <div class="key-row" data-number="${number}" data-kind="text" data-answer="${answer}">
        <div class="key-row-head"><b>${number}-savol</b><span class="subtitle" style="margin:0">yozma</span></div>
        <input class="input key-text-input" value="${answer}" style="margin:0"
               placeholder="Qabul qilinadigan javob(lar), | bilan ajrating" />
      </div>`);
  }

  container.innerHTML = rows.join("");

  container.querySelectorAll('.key-row[data-kind="letters"]').forEach((row) => {
    drawLetterGrid(row);

    row.querySelectorAll("[data-step]").forEach((stepButton) => {
      stepButton.addEventListener("click", () => {
        const next = Number(row.dataset.options) + Number(stepButton.dataset.step);
        if (next < 2 || next > 8) return;

        row.dataset.options = next;
        row.querySelector(".option-count").textContent = next;

        // Variantlar kamaysa, endi mavjud bo'lmagan harflar javobdan olib tashlanadi.
        const lastLetter = String.fromCharCode(64 + next);
        row.dataset.answer = (row.dataset.answer || "")
          .split(",")
          .filter((letter) => letter && letter <= lastLetter)
          .join(",");

        drawLetterGrid(row);
      });
    });
  });

  container.querySelectorAll(".key-text-input").forEach((input) => {
    input.addEventListener("input", () => {
      input.closest(".key-row").dataset.answer = input.value.trim();
    });
  });
}

function drawLetterGrid(row) {
  const grid = row.querySelector(".letter-grid");
  const options = Number(row.dataset.options) || 4;
  const selected = (row.dataset.answer || "").split(",").filter(Boolean);

  grid.innerHTML = Array.from({ length: options }, (_, index) => {
    const letter = String.fromCharCode(65 + index);
    return `<div class="letter-option ${selected.includes(letter) ? "selected" : ""}" data-letter="${letter}">${letter}</div>`;
  }).join("");

  grid.querySelectorAll(".letter-option").forEach((letterEl) => {
    letterEl.addEventListener("click", () => {
      // Bitta harf — bitta to'g'ri javob. Ikkinchisi ham bosilsa, savol
      // "bir nechta to'g'ri javobli" bo'ladi; qayta bosilsa olib tashlanadi.
      const chosen = new Set((row.dataset.answer || "").split(",").filter(Boolean));
      const letter = letterEl.dataset.letter;

      if (chosen.has(letter)) {
        chosen.delete(letter);
      } else {
        chosen.add(letter);
      }

      row.dataset.answer = [...chosen].sort().join(",");
      letterEl.classList.toggle("selected");
    });
  });
}

async function saveNewTest(mode) {
  const titleValue = document.getElementById("field-title").value.trim();
  if (titleValue.length < 2) {
    throw new Error("Test nomini kiriting");
  }

  const requireChannel = document.getElementById("field-require-channel").checked;
  const channelValue = document.getElementById("field-channel").value.trim();

  if (requireChannel && channelValue.length < 2) {
    throw new Error("Kanal nomini kiriting (masalan @kanalingiz)");
  }

  const body = {
    title: titleValue,
    subject: document.getElementById("field-subject").value,
    topic: "",
    price: Number(document.getElementById("field-price").value) || 0,
    duration: Number(document.getElementById("field-duration").value) || 30,
    mode,
    telegram_group_id: null,
    required_channel: requireChannel ? channelValue : null,
    enrollment_type: document.getElementById("enrollment-toggle").dataset.value,
    start_time: localTimeToUtcIso(document.getElementById("field-start-time").value),
    end_time: localTimeToUtcIso(document.getElementById("field-end-time").value),
    questions: [],
    key_questions: [],
  };

  if (mode === "key") {
    const keyRows = [...document.querySelectorAll("#key-questions-container .key-row")];
    if (!keyRows.length) {
      throw new Error("Avval savollar sonini kiriting");
    }

    const emptyRow = keyRows.find((row) => !row.dataset.answer);
    if (emptyRow) {
      throw new Error(`${emptyRow.dataset.number}-savolning to'g'ri javobi belgilanmagan`);
    }

    body.key_questions = keyRows.map((row, index) => {
      const answer = row.dataset.answer;
      const isText = row.dataset.kind === "text";

      return {
        number: index + 1,
        // Savol turi javobning o'zidan aniqlanadi: bitta harf — "single",
        // vergul bilan bir nechta harf — "multi", yozma katak — "text".
        question_type: isText ? "text" : answer.includes(",") ? "multi" : "single",
        option_count: isText ? 4 : Number(row.dataset.options) || 4,
        correct: answer,
      };
    });
  } else {
    body.questions = [...document.querySelectorAll("#questions-container .question-block")].map((block) => {
      const data = {};
      block.querySelectorAll("[data-field]").forEach((field) => {
        data[field.dataset.field] = field.value.trim();
      });
      return data;
    });
  }

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
            <div>
              <b>${escapeHtml(student.name)}</b>
              <p class="subtitle" style="margin:2px 0 0">
                ${student.has_answers
            ? `✅ ${student.correct} to'g'ri • ${student.wrong} xato • ${student.percent}%`
            : "⏳ Javoblar kiritilmagan"
          }
              </p>
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
              ${student.editable
            ? `<button class="icon-button danger" data-delete-student="${student.id}" title="O'chirish">🗑</button>
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
      <span class="subtitle" style="margin:0">Kod: <b>${test.code}</b></span>
    </div>
    ${
      test.required_channel
        ? `<p class="subtitle">📢 Majburiy obuna: <b>${escapeHtml(test.required_channel)}</b></p>`
        : ""
    }

    <button class="btn btn-ghost" id="answer-sheet-button">📄 Javoblar varaqasini olish</button>
    <button class="btn btn-ghost" id="answer-key-button">🔑 To'g'ri javoblar (kalit)</button>
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

  document.getElementById("answer-sheet-button").addEventListener(
    "click",
    guarded(() =>
      deliverPdf(
        `/api/teacher/tests/${testId}/answer-sheet`,
        `${test.code}_javoblar_varaqasi.pdf`,
        "📄 Javoblar varaqasi Telegramga yuborildi"
      )
    )
  );

  document.getElementById("answer-key-button").addEventListener(
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
          ? `<input class="input" data-text-ref="${question.ref}" value="${escapeHtml(current)}" placeholder="Javobini yozing" />`
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

  document.querySelectorAll("[data-text-ref]").forEach((input) => {
    input.addEventListener("input", () => {
      answers[input.dataset.textRef] = input.value;
    });
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
    <p>⏱️ O'rtacha vaqt: <b>${formatMinutesSeconds(stats.avg_seconds)}</b></p>
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
