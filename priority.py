"""Приоритет и московский фильтр.

Оба вычисляются детерминированно по уже извлечённым фактам — сумме,
площади, типу события, — а не спрашиваются у модели напрямую. Модель
плохо оценивает «важность», это субъективное суждение, которое будет
скакать от текста к тексту. Зато сумму и площадь она извлекает надёжно,
и по ним можно посчитать вес формулой, которую видно и можно поправить.

Здесь же — подстраховка для поля done. Модели дана инструкция не путать
несостоявшуюся сделку с состоявшейся, но инструкция не железная: на
формулировках вроде «сорвались переговоры» модель иногда путается.
Поверх ответа модели проверяем текст на явные отрицания и принудительно
сбрасываем done, если они есть — тем же приёмом, что PASSIVE_VERBS
в offline.py разбирает безличные обороты.
"""

import re

import refs

# ---------------------------------------------------------- done: подстраховка

NOT_DONE_MARKERS = (
    "не смог", "не удалось", "не нашл", "не найден покупат",
    "сорвал", "приостановлен", "приостановил",
    "отменен", "отменён", "отменил",
    "не состоял", "признан несостояв", "признаны несостояв",
    "затянул", "не завершен", "не завершён", "заморожен", "заморозил",
    "не смогла найти покупателя", "покупатель не найден",
)


def looks_not_done(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in NOT_DONE_MARKERS)


# --------------------------------------------------------------- приоритет

# Банкротство и суд важны независимо от суммы — это сигнал риска,
# а не размера сделки.
ALWAYS_HIGH = {"банкротство", "суд"}

# Вехи проекта: даже без суммы это значимое событие для девелопера.
MILESTONE_KINDS = {"тэп", "рнс", "рнв", "старт продаж"}

# Пороги — обычные числа, без обращения к модели. Поправить, если
# в потоке систематически много сделок выше/ниже привычного диапазона.
LARGE_AMOUNT = 3_000_000_000     # 3 млрд руб — высокий приоритет
MEDIUM_AMOUNT = 500_000_000      # 500 млн руб — средний
LARGE_AREA = 10_000              # 10 тыс кв м (или от 1 га) — высокий
MEDIUM_AREA = 1_000              # меньше — типичная мелкая аренда

_AMOUNT_RE = re.compile(
    r"(\d[\d\s]*[.,]?\d*)\s*(млрд|млн|тыс)?", re.IGNORECASE
)
_AREA_RE = re.compile(
    r"(\d[\d\s]*[.,]?\d*)\s*(тыс\.?\s*)?(кв\.?\s*м|га)", re.IGNORECASE
)


def _to_rub(amount: str | None) -> float | None:
    if not amount:
        return None
    m = _AMOUNT_RE.search(amount)
    if not m or not m.group(1).strip():
        return None
    try:
        num = float(m.group(1).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    mult = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3}.get((m.group(2) or "").lower(), 1)
    return num * mult


def _to_sqm(area: str | None) -> float | None:
    if not area:
        return None
    m = _AREA_RE.search(area)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    if m.group(2):  # «тыс.»
        num *= 1000
    if "га" in m.group(3).lower():
        num *= 10_000  # 1 га = 10 000 кв м
    return num


def score(kind: str | None, amount: str | None, area: str | None) -> str:
    """Возвращает 'высокий' | 'средний' | 'низкий'."""
    k = kind or ""

    if k in ALWAYS_HIGH:
        return "высокий"

    rub = _to_rub(amount)
    sqm = _to_sqm(area)

    if rub is not None:
        if rub >= LARGE_AMOUNT:
            return "высокий"
        if rub >= MEDIUM_AMOUNT:
            return "средний"

    if sqm is not None:
        if sqm >= LARGE_AREA:
            return "высокий"
        if sqm >= MEDIUM_AREA:
            return "средний"
        if rub is None:
            # площадь маленькая, денег нет — типичная мелкая аренда офиса
            return "низкий"

    if k in MILESTONE_KINDS:
        return "средний"

    if rub is None and sqm is None:
        # размер сделки неизвестен — не занижаем на всякий случай:
        # часто крупные сделки не раскрывают сумму именно потому,
        # что она большая
        return "средний"

    return "низкий"


# ------------------------------------------------------------ фильтр Москвы

# Сама сверка адреса живёт в refs.py — рядом со справочником районов,
# магистралей и округов, по которому она и работает. Здесь только
# порядок, в котором спрашиваем.


def is_moscow(district: str | None, okrug: str | None,
              location: str | None, text: str = "") -> bool:
    """Мягкий фильтр: прячем только то, что явно про другой город.

    Пустое или непонятное — считаем Москвой. Лучше лишняя карточка,
    чем потерянная московская сделка из-за того, что в тексте не
    встретилось слово «Москва».
    """
    if okrug:  # округ уже определён где-то раньше — точно Москва
        return True

    # Сначала адресные поля: они точнее всего остального. Если в адресе
    # стоит другой город, упоминание Москвы дальше по тексту («в отличие
    # от Москвы», «московские аналитики») его не отменяет.
    verdict = refs.city_verdict(" ".join(filter(None, [district, location])))
    if verdict:
        return verdict == "moscow"

    # Адрес молчит — смотрим весь текст новости: там может найтись
    # магистраль или район, по которым адрес и опознаётся.
    verdict = refs.city_verdict(text)
    if verdict:
        return verdict == "moscow"

    return True


# ------------------------------------------------------------ сведение вместе

def finalize(data: dict, title: str, text: str = "") -> dict:
    """Дописывает в результат разбора priority и is_moscow, страхует done.

    Вызывается один раз после того, как получен ответ модели (или правил),
    независимо от режима — вся логика приоритета живёт в одном месте.
    """
    if not data.get("relevant"):
        return data

    if data.get("done") and looks_not_done(f"{title} {text[:400]}"):
        data["done"] = False

    data["priority"] = score(data.get("kind"), data.get("amount"),
                             data.get("area"))
    data["is_moscow"] = is_moscow(data.get("district"), data.get("okrug"),
                                  data.get("location"), f"{title} {text}")
    return data
