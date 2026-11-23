"""
Kichik LaTeX chizgich (reportlab): kalitdagi yozma javoblarni kitobdagidek chizadi —
kasr (\\frac), ildiz (\\sqrt, \\sqrt[3]), daraja/indeks (^, _), yunon harflari va belgilar.
Tushunmagan ifodada oddiy matnga qaytadi (hech qachon xato bermaydi).
"""

from reportlab.pdfbase.pdfmetrics import stringWidth

SYMBOLS = {
    "pi": "π", "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "infty": "∞", "circ": "°",
    "degree": "°", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "approx": "≈",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "theta": "θ", "lambda": "λ", "mu": "μ",
    "sigma": "σ", "phi": "φ", "varphi": "φ", "omega": "ω", "Delta": "Δ", "Omega": "Ω", "angle": "∠",
    "perp": "⊥", "parallel": "∥", "in": "∈", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "to": "→", "rightarrow": "→", "exponentialE": "e", "imaginaryI": "i", "%": "%", "{": "{", "}": "}",
    "|": "‖", ",": " ", ";": " ", ":": " ", "!": "", " ": " ", "quad": "  ", "qquad": "    ",
}
FUNCTIONS = {"log", "ln", "lg", "sin", "cos", "tan", "tg", "ctg", "cot", "arcsin", "arccos", "arctan", "arctg",
             "lim", "min", "max", "exp"}
TRANSPARENT = {"displaystyle", "textstyle", "mathrm", "text", "operatorname", "mathbf", "mathit", "boxed"}
OPERATORS = set("+−=×÷±≤≥≠≈<>")


class Box:
    w = asc = desc = 0.0

    def draw(self, canvas, x, y):  # y — tayanch chiziq (baseline)
        pass


class Text(Box):
    def __init__(self, text, font, size, color, pad=0.0):
        self.text, self.font, self.size, self.color, self.pad = text, font, size, color, pad
        self.w = stringWidth(text, font, size) + 2 * pad
        self.asc, self.desc = size * 0.74, size * 0.24

    def draw(self, canvas, x, y):
        canvas.setFillColor(self.color)
        canvas.setFont(self.font, self.size)
        canvas.drawString(x + self.pad, y, self.text)


class Row(Box):
    def __init__(self, items):
        self.items = items
        self.w = sum(item.w for item in items)
        self.asc = max([item.asc for item in items] or [0])
        self.desc = max([item.desc for item in items] or [0])

    def draw(self, canvas, x, y):
        for item in self.items:
            item.draw(canvas, x, y)
            x += item.w


class Frac(Box):
    def __init__(self, num, den, size, color):
        self.num, self.den, self.color = num, den, color
        self.pad, self.rule = size * 0.14, max(0.6, size * 0.05)
        self.axis, self.gap = size * 0.30, size * 0.13
        self.w = max(num.w, den.w) + 2 * self.pad
        self.asc = self.axis + self.rule / 2 + self.gap + num.desc + num.asc
        self.desc = den.asc + self.gap + self.rule / 2 - self.axis + den.desc

    def draw(self, canvas, x, y):
        self.num.draw(canvas, x + (self.w - self.num.w) / 2, y + self.axis + self.rule / 2 + self.gap + self.num.desc)
        self.den.draw(canvas, x + (self.w - self.den.w) / 2, y + self.axis - self.rule / 2 - self.gap - self.den.asc)
        canvas.setStrokeColor(self.color)
        canvas.setLineWidth(self.rule)
        canvas.line(x + self.pad * 0.35, y + self.axis, x + self.w - self.pad * 0.35, y + self.axis)


class Sqrt(Box):
    def __init__(self, body, index, size, color):
        self.body, self.index, self.color, self.size = body, index, color, size
        self.rule, self.gap = max(0.6, size * 0.055), size * 0.14
        self.sign = size * 0.55
        self.shift = max(0.0, index.w - self.sign * 0.45) if index else 0.0
        self.asc = max(body.asc, size * 0.72) + self.gap + self.rule
        self.desc = max(body.desc, size * 0.1)
        self.w = self.shift + self.sign + body.w + size * 0.1

    def draw(self, canvas, x, y):
        bottom, top = y - self.desc, y + self.asc - self.rule / 2
        h, x0 = top - bottom, x + self.shift
        canvas.setStrokeColor(self.color)
        canvas.setLineWidth(self.rule)
        canvas.setLineJoin(1)
        path = canvas.beginPath()
        path.moveTo(x0, bottom + 0.42 * h)
        path.lineTo(x0 + 0.22 * self.sign, bottom + 0.52 * h)
        path.lineTo(x0 + 0.52 * self.sign, bottom)
        path.lineTo(x0 + self.sign, top)
        path.lineTo(x + self.w, top)
        canvas.drawPath(path, stroke=1, fill=0)
        if self.index:
            self.index.draw(canvas, x, bottom + 0.58 * h)
        self.body.draw(canvas, x0 + self.sign + self.size * 0.05, y)


class Script(Box):
    def __init__(self, base, sup, sub, size):
        self.base, self.sup, self.sub = base, sup, sub
        self.raise_ = max(size * 0.42, base.asc - (sup.asc * 0.6 if sup else 0))
        self.drop = max(size * 0.2, base.desc)
        self.w = base.w + max(sup.w if sup else 0, sub.w if sub else 0) + size * 0.04
        self.asc = max(base.asc, self.raise_ + sup.asc if sup else 0)
        self.desc = max(base.desc, self.drop + sub.desc if sub else 0)

    def draw(self, canvas, x, y):
        self.base.draw(canvas, x, y)
        if self.sup:
            self.sup.draw(canvas, x + self.base.w + 0.5, y + self.raise_)
        if self.sub:
            self.sub.draw(canvas, x + self.base.w + 0.5, y - self.drop)


class _Parser:
    def __init__(self, source, fonts, color):
        self.s, self.i = source, 0
        self.font, self.italic = fonts
        self.color = color

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def command(self):
        self.i += 1  # "\"
        start = self.i
        if self.i < len(self.s) and self.s[self.i].isalpha():
            while self.i < len(self.s) and self.s[self.i].isalpha():
                self.i += 1
        else:
            self.i += 1
        return self.s[start:self.i]

    def group(self, size):
        while self.peek() == " ":
            self.i += 1
        if self.peek() == "{":
            self.i += 1
            row = self.row(size, until="}")
            self.i += 1
            return row
        return self.atom(size) or Row([])

    def text(self, chars, size):
        items, run, italic = [], "", None

        def flush():
            nonlocal run
            if run:
                items.append(Text(run, self.italic if italic else self.font, size, self.color))
            run = ""

        for char in chars:
            char = {"-": "−", "*": "·"}.get(char, char)
            if char in OPERATORS:
                flush()
                items.append(Text(char, self.font, size, self.color, pad=size * 0.16))
                continue
            is_italic = char.isascii() and char.isalpha()
            if italic is not None and is_italic != italic:
                flush()
            italic = is_italic
            run += char
        flush()
        return items

    def atom(self, size):
        char = self.peek()
        if not char:
            return None
        if char == "{":
            return self.group(size)
        if char == "\\":
            name = self.command()
            if name in ("frac", "dfrac", "tfrac"):
                inner = max(size * 0.86, 6)
                return Frac(self.group(inner), self.group(inner), size, self.color)
            if name == "sqrt":
                index = None
                if self.peek() == "[":
                    self.i += 1
                    index = self.row(size * 0.6, until="]")
                    self.i += 1
                return Sqrt(self.group(size), index, size, self.color)
            if name in ("left", "right"):
                nxt = self.peek()
                self.i += 1
                if nxt == "\\":
                    self.i -= 1
                    return self.atom(size)
                return Row(self.text(nxt, size)) if nxt and nxt != "." else Row([])
            if name == "placeholder":
                self.group(size)
                return Row([])
            if name in TRANSPARENT:
                return self.group(size)
            if name in FUNCTIONS:
                return Text(name, self.font, size, self.color, pad=size * 0.06)
            if name in SYMBOLS:
                return Row(self.text(SYMBOLS[name], size))
            return Text(name, self.font, size, self.color)
        self.i += 1
        if char == " ":
            return Row([])
        return Row(self.text(char, size))

    def row(self, size, until=""):
        items = []
        while self.i < len(self.s) and self.peek() != until:
            if self.peek() in "^_" and items:
                sup = sub = None
                while self.peek() in ("^", "_") and self.peek():
                    which = self.peek()
                    self.i += 1
                    box = self.group(size * 0.7)
                    if which == "^":
                        sup = box
                    else:
                        sub = box
                items[-1] = Script(items[-1], sup, sub, size)
                continue
            atom = self.atom(size)
            if atom is None:
                break
            # Matnni harf-harf emas, bitta bo'lak qilib qo'shamiz (keyingi ^ faqat oxirgi belgiga tegsin)
            if isinstance(atom, Row) and all(isinstance(item, Text) for item in atom.items) and len(atom.items) == 1:
                atom = atom.items[0]
            items.append(atom)
        return Row(items)


def math_box(latex: str, size: float, fonts: tuple[str, str], color, plain=None) -> Box:
    """LaTeX ifodaning chizma qutisi. Xato bo'lsa — oddiy matn (plain(latex))."""

    source = (latex or "").strip()
    try:
        parser = _Parser(source, fonts, color)
        box = parser.row(size)
        if parser.i < len(source):
            raise ValueError("ortiqcha belgi")
        return box
    except Exception:
        return Text(plain(source) if plain else source, fonts[0], size, color)


def fit(box_factory, size: float, max_width: float, min_size: float = 6.5):
    """Katakka sig'masa — shriftni kichraytirib qayta chizadi."""

    box = box_factory(size)
    while box.w > max_width and size > min_size:
        size -= 0.5
        box = box_factory(size)
    return box
