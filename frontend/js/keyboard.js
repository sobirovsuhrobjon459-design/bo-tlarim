/*
  Matematik klaviatura maketi (MathLive uchun).

  Desmos'nikiga o'xshash, lekin o'zimizniki: chapda matematik belgilar, o'ngda
  raqamlar paneli, eng o'ngda harakat tugmalari. Ikkita bo'lim bor va ular
  klaviatura tepasidagi yorliqlar orqali almashadi:

    "123"  — kundalik yozuv: raqamlar, amallar, ildiz, daraja, qavs
    "f(x)" — funksiyalar: sin, cos, tg, log, ln, darajalar, ildizlar
    "ABC"  — oddiy harflar (MathLive'ning tayyor maketi)

  Nega alohida fayl: maket — bu sof ma'lumot, mantiq emas. Uni app.js ichiga
  qo'shsak, fayl yanada uzayib ketardi va klaviaturani tahrirlash qiyinlashardi.

  ---------------------------------------------------------------------------
  Tugmani qanday yozish kerak:

    { latex: "\\pi" }                     — belgini ham ko'rsatadi, ham qo'yadi
    { latex: "\\sin", insert: "\\sin(#?)" } — "sin" deb ko'rinadi, "sin(▢)" qo'yadi
    { latex: "a^b", insert: "#@^{#?}" }         — "aᵇ" deb ko'rinadi, darajaga ko'taradi

  O'rin tutuvchi belgilar:
    #?  — bo'sh katak (kursor shu yerga tushadi)
    #0  — belgilangan matn (hech narsa belgilanmagan bo'lsa — bo'sh katak)
    #@  — kursordan oldingi belgi (daraja, kvadrat kabi "orqaga" amallar uchun)

  "[7]", "[+]", "[backspace]" kabi kvadrat qavsli nomlar — MathLive'ning tayyor
  tugmalari. "[separator-5]" esa ustunlar orasidagi bo'shliq.
  ---------------------------------------------------------------------------
*/

// Har bir qatorning tuzilishi bir xil: 4 ta matematik + bo'shliq +
// 4 ta raqam paneli + bo'shliq + 1 ta harakat tugmasi. Shunda ustunlar
// tik chiziqda turadi va barmoq adashmaydi.

const NUMERIC_LAYOUT = {
  label: "123",
  tooltip: "Raqamlar va amallar",
  id: "uz-numeric",
  rows: [
    [
      { latex: "x", variants: ["y", "z", "t", "n", "a", "b"] },
      { latex: "y", variants: ["x", "z", "t", "k", "m"] },
      { latex: "a^2", insert: "#@^{2}", tooltip: "Kvadrat" },
      { latex: "a^b", insert: "#@^{#?}", tooltip: "Daraja" },
      "[separator-5]",
      "[7]",
      "[8]",
      "[9]",
      "[/]",
      "[separator-5]",
      "[backspace]",
    ],
    [
      "[(]",
      "[)]",
      { latex: "\\sqrt{#0}", tooltip: "Kvadrat ildiz" },
      { latex: "\\sqrt[#?]{#0}", tooltip: "n-darajali ildiz" },
      "[separator-5]",
      "[4]",
      "[5]",
      "[6]",
      "[*]",
      "[separator-5]",
      "[left]",
    ],
    [
      { latex: "\\frac{#0}{#?}", tooltip: "Kasr" },
      { latex: "\\left|#0\\right|", tooltip: "Modul" },
      { label: "&lt;", latex: "<" },
      { label: "&gt;", latex: ">" },
      "[separator-5]",
      "[1]",
      "[2]",
      "[3]",
      "[-]",
      "[separator-5]",
      "[right]",
    ],
    [
      { latex: "\\pi", variants: ["\\tau", "\\theta", "\\alpha", "\\beta"] },
      { latex: "\\exponentialE", tooltip: "Eyler soni", variants: ["\\exponentialE^{#?}", "\\ln", "\\log"] },
      { label: "&le;", latex: "\\le" },
      { label: "&ge;", latex: "\\ge" },
      "[separator-5]",
      "[0]",
      "[.]",
      "[=]",
      "[+]",
      "[separator-5]",
      "[action]",
    ],
  ],
};

const FUNCTIONS_LAYOUT = {
  label: "f(x)",
  tooltip: "Funksiyalar",
  id: "uz-functions",
  rows: [
    [
      // Uzun bosilganda muqobil yozilishlar chiqadi: tg uchun tan, ctg uchun cot.
      { latex: "\\sin", insert: "\\sin(#?)", variants: ["\\sinh", "\\arcsin", "\\sin^{-1}"] },
      { latex: "\\cos", insert: "\\cos(#?)", variants: ["\\cosh", "\\arccos", "\\cos^{-1}"] },
      {
        label: "tg",
        insert: "\\operatorname{tg}(#?)",
        variants: ["\\tan", "\\tanh", "\\arctan", { label: "arctg", insert: "\\operatorname{arctg}(#?)" }],
      },
      {
        label: "ctg",
        insert: "\\operatorname{ctg}(#?)",
        variants: ["\\cot", "\\coth", { label: "arcctg", insert: "\\operatorname{arcctg}(#?)" }],
      },
      "[separator-5]",
      { latex: "\\sqrt{#0}", tooltip: "Kvadrat ildiz" },
      { latex: "\\sqrt[3]{#0}", tooltip: "Kub ildiz" },
      { latex: "\\sqrt[#?]{#0}", tooltip: "n-darajali ildiz" },
      { latex: "a^b", insert: "#@^{#?}", tooltip: "Daraja" },
      "[separator-5]",
      "[backspace]",
    ],
    [
      { label: "arcsin", class: "small", insert: "\\arcsin(#?)" },
      { label: "arccos", class: "small", insert: "\\arccos(#?)" },
      { label: "arctg", class: "small", insert: "\\operatorname{arctg}(#?)" },
      { latex: "\\pi", variants: ["\\tau", "\\theta", "\\varphi", "\\alpha", "\\beta", "\\gamma"] },
      "[separator-5]",
      { latex: "\\log", insert: "\\log(#?)", tooltip: "O'nlik logarifm" },
      { latex: "\\ln", insert: "\\ln(#?)", tooltip: "Natural logarifm" },
      { latex: "\\log_a", insert: "\\log_{#?}(#?)", tooltip: "Asosi a bo'lgan logarifm" },
      { label: "lg", insert: "\\lg(#?)" },
      "[separator-5]",
      "[left]",
    ],
    [
      { latex: "\\exponentialE" },
      { latex: "e^x", insert: "\\exponentialE^{#?}" },
      { latex: "10^x", insert: "10^{#?}" },
      { latex: "\\infty" },
      "[separator-5]",
      { latex: "\\frac{#0}{#?}", tooltip: "Kasr" },
      { latex: "\\left|#0\\right|", tooltip: "Modul" },
      { latex: "\\pm" },
      { label: "%", latex: "\\%" },
      "[separator-5]",
      "[right]",
    ],
    [
      { label: "&ne;", latex: "\\ne" },
      { label: "&asymp;", latex: "\\approx" },
      { label: "&le;", latex: "\\le" },
      { label: "&ge;", latex: "\\ge" },
      "[separator-5]",
      "[(]",
      "[)]",
      { label: "&deg;", latex: "\\degree", tooltip: "Daraja (burchak)" },
      { label: "!", latex: "!", tooltip: "Faktorial" },
      "[separator-5]",
      "[action]",
    ],
  ],
};

// app.js shu ro'yxatni o'qiydi. "alphabetic" — MathLive'ning tayyor harf maketi.
window.MATH_KEYBOARD_LAYOUTS = [NUMERIC_LAYOUT, FUNCTIONS_LAYOUT, "alphabetic"];
