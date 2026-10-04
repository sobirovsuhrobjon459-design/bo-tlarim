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

// localStorage ishlatiladi (sessionStorage emas) — shunda Mini App yopib qayta
// ochilganda ham o'qituvchi login/parolni qayta kiritmaydi (YouTube'dagidek).
let teacherToken = localStorage.getItem("teacherToken") || "";
let teacherUsername = localStorage.getItem("teacherUsername") || "";
let isAdmin = false;
let quizState = null;
let quizTimerId = null;

/* ---------- 1) Yordamchi funksiyalar ---------- */

async function api(path, { method = "GET", body } = {}) {
  const headers = {
    "X-Init-Data": telegram?.initData || "",
    "X-Teacher-Token": teacherToken,
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

    ${
      isAdmin
        ? `<div class="card card-entrance" data-action="admin">
             <div class="card-icon">👑</div>
             <div class="card-body"><h3>ADMIN</h3><p>Umumiy statistika va boshqaruv</p></div>
           </div>`
        : ""
    }

    ${statusHtml}
  `);

  document.querySelector('[data-action="student"]').addEventListener("click", renderStudentSearch);
  document.querySelector('[data-action="teacher"]').addEventListener("click", guarded(enterTeacherArea));
  document.querySelector('[data-action="admin"]')?.addEventListener("click", guarded(renderAdminDashboard));
}

async function loadHome() {
  renderSkeleton();
  try {
    const me = await api("/api/me");
    isAdmin = Boolean(me.is_admin);
    renderHome(me.full_name || null, true);
  } catch (error) {
    isAdmin = false;
    renderHome(null, false);
  }
}

async function renderAdminDashboard() {
  const [stats, topStudents] = await Promise.all([api("/api/admin/stats"), api("/api/admin/top-students")]);

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
    <h3>🏆 Eng yaxshi o'quvchilar (barcha testlar bo'yicha)</h3>
    ${topRows}
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
}

/* ---------- 3) O'quvchi oqimi ---------- */

function renderStudentSearch() {
  showPage(`
    <button class="back-button" data-action="home">←</button>
    <h2>O'quvchi</h2>
    <input
      class="input"
      id="test-code-input"
      placeholder="Test kodini kiriting..."
      style="text-transform:uppercase;text-align:center;letter-spacing:2px;font-size:18px"
    />
    <button class="btn" id="search-button">🔍 Qidirish</button>
    <div id="search-result"></div>
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
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
  const actionButton = test.paid
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

  const selectedAnswer = quizState.answers[question.number] || "";
  const selectedLetters = new Set(selectedAnswer ? selectedAnswer.split(",") : []);

  const optionsHtml = Array.from({ length: question.option_count }, (_, index) => {
    const letter = String.fromCharCode(65 + index);
    const selected = selectedLetters.has(letter) ? "selected" : "";
    return `<div class="option ${selected}" data-letter="${letter}" style="text-align:center"><b>${letter}</b></div>`;
  }).join("");

  showPage(`
    <div class="row-between">
      <b>${question.number}-savol / ${total}</b>
      <b id="quiz-timer"></b>
    </div>
    <div class="progress-track"><div class="progress-fill" style="width:${progressPercent}%"></div></div>
    <p class="subtitle">${isMulti ? "Bir nechta to'g'ri javobni tanlang" : "To'g'ri javobni tanlang"}</p>
    <div id="options-container" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(60px,1fr));gap:10px">${optionsHtml}</div>
    <button class="btn" id="next-button" style="margin-top:16px">${isLast ? "🏁 TESTNI YAKUNLASH" : "Keyingi →"}</button>
  `);

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

  document.querySelector('[data-action="student"]').addEventListener("click", renderStudentSearch);
}

/* ---------- 4) O'qituvchi oqimi ---------- */

async function enterTeacherArea() {
  if (teacherToken && teacherUsername) {
    renderTeacherWelcomeBack();
  } else {
    renderTeacherAuth();
  }
}

function renderTeacherWelcomeBack() {
  showPage(`
    <button class="back-button" data-action="home">←</button>
    <h2>O'qituvchi</h2>
    <div class="card" style="cursor:default">
      <div class="card-icon">👤</div>
      <div class="card-body">
        <h3>${escapeHtml(teacherUsername)}</h3>
        <p>Oldin shu akkaunt bilan kirgansiz</p>
      </div>
    </div>
    <button class="btn" id="continue-button">Davom etish →</button>
    <button class="btn btn-ghost" id="switch-account-button">Boshqa akkaunt bilan kirish</button>
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  document.getElementById("continue-button").addEventListener("click", guarded(renderTeacherDashboard));
  document.getElementById("switch-account-button").addEventListener("click", () => {
    clearRememberedTeacher();
    renderTeacherAuth();
  });
}

function clearRememberedTeacher() {
  teacherToken = "";
  teacherUsername = "";
  localStorage.removeItem("teacherToken");
  localStorage.removeItem("teacherUsername");
}

function renderTeacherAuth() {
  showPage(`
    <button class="back-button" data-action="home">←</button>
    <h2>O'qituvchi kirishi</h2>
    <input class="input" id="username-input" placeholder="Login (lotin harf/raqam)" />
    <input class="input" id="password-input" type="password" placeholder="Parol (kamida 6 belgi)" />
    <button class="btn" id="login-button">Kirish</button>
    <button class="btn btn-ghost" id="register-button">Ro'yxatdan o'tish</button>
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  document.getElementById("login-button").addEventListener("click", guarded(() => submitTeacherAuth("login")));
  document.getElementById("register-button").addEventListener("click", guarded(() => submitTeacherAuth("register")));
}

async function submitTeacherAuth(mode) {
  const username = document.getElementById("username-input").value.trim();
  const password = document.getElementById("password-input").value;

  const result = await api(`/api/auth/${mode}`, { method: "POST", body: { username, password } });
  teacherToken = result.token;
  teacherUsername = username;
  localStorage.setItem("teacherToken", teacherToken);
  localStorage.setItem("teacherUsername", teacherUsername);
  await renderTeacherDashboard();
}

async function renderTeacherDashboard() {
  let stats;
  let tests;

  try {
    [stats, tests] = await Promise.all([api("/api/teacher/stats"), api("/api/teacher/tests")]);
  } catch (error) {
    clearRememberedTeacher();
    throw error;
  }

  const testCards =
    tests
      .map(
        (test) => `
          <div class="card card-entrance" data-test-id="${test.id}">
            <div class="card-body">
              <h3>${escapeHtml(test.title)} ${test.mode === "key" ? "🔑" : ""}</h3>
              <p>${escapeHtml(test.subject)} • ${test.count} savol • ${test.price ? formatNumber(test.price) + " so'm" : "Bepul"}</p>
              <p>Kod: <b>${test.code}</b> • 👥 ${test.students} qatnashchi</p>
            </div>
          </div>`
      )
      .join("") || "<p>Hali testlar yo'q</p>";

  showPage(`
    <button class="back-button" data-action="home">←</button>
    <h2>Dashboard</h2>
    <div class="stats-grid">
      <div class="stat-tile">📚<span class="stat-value">${stats.tests}</span>Testlar</div>
      <div class="stat-tile">👥<span class="stat-value">${stats.students}</span>O'quvchilar</div>
      <div class="stat-tile">📊<span class="stat-value">${stats.avg}%</span>O'rtacha natija</div>
      <div class="stat-tile">🏆<span class="stat-value">${stats.max}%</span>Eng yuqori</div>
    </div>
    <button class="btn" id="new-test-button">➕ YANGI TEST YARATISH</button>
    ${testCards}
    <p class="status">Backend ishlayapti ✅</p>
  `);

  document.querySelector('[data-action="home"]').addEventListener("click", loadHome);
  document.getElementById("new-test-button").addEventListener("click", () => renderNewTestForm("full"));
  document.querySelectorAll("[data-test-id]").forEach((card) => {
    card.addEventListener("click", guarded(() => renderTestResults(Number(card.dataset.testId))));
  });
}

function renderNewTestForm(mode) {
  const modeTabs = `
    <div class="row-between" style="gap:8px;margin-bottom:10px">
      <button class="btn ${mode === "full" ? "" : "btn-ghost"}" id="mode-full-button" style="flex:1">📝 To'liq savol</button>
      <button class="btn ${mode === "key" ? "" : "btn-ghost"}" id="mode-key-button" style="flex:1">🔑 Javoblar kaliti</button>
    </div>
  `;

  const commonFields = `
    <input class="input" id="field-title" placeholder="Test nomi" />
    <input class="input" id="field-subject" placeholder="Fan" />
    <input class="input" id="field-topic" placeholder="Mavzu" />
    <input class="input" id="field-price" type="number" placeholder="Narxi (so'm, 0 = bepul)" value="0" />
    <input class="input" id="field-duration" type="number" placeholder="Davomiyligi (daqiqa)" value="30" />
  `;

  if (mode === "key") {
    showPage(`
      <button class="back-button" data-action="dashboard">←</button>
      <h2>Yangi test</h2>
      ${modeTabs}
      ${commonFields}
      <h3>Javoblar kaliti</h3>
      <p class="subtitle">
        Savol matni kerak emas — u tashqarida (masalan, kanalingizda) joylashtirilgan bo'ladi.
        Har bir savol uchun faqat turini, variantlar sonini va to'g'ri javobni kiriting.
      </p>
      <div id="key-questions-container"></div>
      <button class="btn btn-ghost" id="add-key-question-button">➕ Savol qo'shish</button>
      <button class="btn" id="save-test-button">💾 Testni saqlash</button>
    `);
  } else {
    showPage(`
      <button class="back-button" data-action="dashboard">←</button>
      <h2>Yangi test</h2>
      ${modeTabs}
      ${commonFields}
      <h3>Savollar</h3>
      <div id="questions-container"></div>
      <button class="btn btn-ghost" id="add-question-button">➕ Savol qo'shish</button>
      <button class="btn" id="save-test-button">💾 Testni saqlash</button>
    `);
  }

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));
  document.getElementById("mode-full-button").addEventListener("click", () => renderNewTestForm("full"));
  document.getElementById("mode-key-button").addEventListener("click", () => renderNewTestForm("key"));

  if (mode === "key") {
    document.getElementById("add-key-question-button").addEventListener("click", addKeyQuestionBlock);
    document.getElementById("save-test-button").addEventListener("click", guarded(() => saveNewTest("key")));
    addKeyQuestionBlock();
  } else {
    document.getElementById("add-question-button").addEventListener("click", addQuestionBlock);
    document.getElementById("save-test-button").addEventListener("click", guarded(() => saveNewTest("full")));
    addQuestionBlock();
  }
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

function addKeyQuestionBlock() {
  const container = document.getElementById("key-questions-container");
  const number = container.querySelectorAll(".question-block").length + 1;

  const block = document.createElement("div");
  block.className = "question-block";
  block.innerHTML = `
    <div class="row-between">
      <b class="question-number">${number}-savol</b>
      <button class="btn btn-ghost" type="button" data-action="remove-question" style="width:auto;padding:6px 12px">🗑</button>
    </div>
    <select class="input" data-field="question_type">
      <option value="single">Bitta to'g'ri javob</option>
      <option value="multi">Bir nechta to'g'ri javob</option>
    </select>
    <input class="input" data-field="option_count" type="number" min="2" max="8" value="4" placeholder="Variantlar soni" />
    <input class="input" data-field="correct" placeholder="To'g'ri javob (masalan B yoki A,C)" style="text-transform:uppercase" />
  `;
  block.querySelector('[data-action="remove-question"]').addEventListener("click", () => {
    block.remove();
    container.querySelectorAll(".question-block").forEach((item, index) => {
      item.querySelector(".question-number").textContent = `${index + 1}-savol`;
    });
  });
  container.appendChild(block);
}

async function saveNewTest(mode) {
  const body = {
    title: document.getElementById("field-title").value.trim(),
    subject: document.getElementById("field-subject").value.trim(),
    topic: document.getElementById("field-topic").value.trim(),
    price: Number(document.getElementById("field-price").value) || 0,
    duration: Number(document.getElementById("field-duration").value) || 30,
    mode,
    questions: [],
    key_questions: [],
  };

  if (mode === "key") {
    body.key_questions = [...document.querySelectorAll("#key-questions-container .question-block")].map(
      (block, index) => ({
        number: index + 1,
        question_type: block.querySelector('[data-field="question_type"]').value,
        option_count: Number(block.querySelector('[data-field="option_count"]').value) || 4,
        correct: block.querySelector('[data-field="correct"]').value.trim().toUpperCase(),
      })
    );
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
  await renderTeacherDashboard();
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
    <button class="back-button" data-action="dashboard">←</button>
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

  document.querySelector('[data-action="dashboard"]').addEventListener("click", guarded(renderTeacherDashboard));
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
  if (telegram?.initData) {
    await api(`/api/teacher/tests/${testId}/pdf/send`, { method: "POST", body: {} });
    showToast("📄 PDF Telegram chatga yuborildi");
    return;
  }

  const response = await fetch(`/api/teacher/tests/${testId}/pdf`, {
    headers: { "X-Teacher-Token": teacherToken, "X-Dev-User": DEV_USER_ID },
  });
  if (!response.ok) throw new Error("PDF yuklab bo'lmadi");

  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "hisobot.pdf";
  link.click();
}

/* ---------- Ishga tushirish ---------- */

loadHome();
