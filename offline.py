"""Бесплатный разбор — правилами, без обращения к ИИ.

Работает на том, что заголовки cre.ru довольно однотипны:
«кто-то» + действие + объект + где + сколько. Это не заменяет модель:
сложные формулировки правила не осилят, и поля останутся пустыми.
Зато не стоит ничего и работает мгновенно.
"""

import re

# Действия покупающей стороны и продающей. Порядок важен: более длинные
# формы идут раньше, чтобы «закрыл сделку по продаже» не попал в «продал».
BUY_VERBS = [
    "закрыл сделку по покупке",
    "закрыла сделку по покупке",
    "приобрёл",
    "приобрел",
    "приобрела",
    "купил",
    "купила",
    "арендовал",
    "арендовала",
    "займёт",
    "займет",
    "снял",
    "выкупил",
    "выкупила",
    "стал владельцем",
    "стала владельцем",
    "стал собственником",
    "получил в собственность",
    "вошёл в проект",
    "вошел в проект",
    "войдёт в проект",
    "войдет в проект",
]

SELL_VERBS = [
    "закрыл сделку по продаже",
    "закрыла сделку по продаже",
    "выставил на продажу",
    "выставила на продажу",

    "продал",
    "продала",
    "продаёт",
    "продает",
    "передаст",
    "передал",
    "передала",
    "реализует",
    "вышел из проекта",
    "вышла из проекта",
    "сдал в аренду",
    "избавился от",
    "избавилась от",
]

# Безличные обороты: слева стоит не сторона сделки, а сам объект
PASSIVE_VERBS = [
    "выставят на аукцион",
    "выставили на аукцион",
    "выставят на продажу",
    "выставили на продажу",
    "выставлен на продажу",
    "сменил владельца",
    "сменит владельца",
    "упакуют в зпиф",
    "выставили на торги",
    "выставят на торги",
    "выставлен на торги",
    "сменил собственника",
    "уйдёт с молотка",
    "уйдет с молотка",
]

RENT_MARKERS = ("аренд", "снял", "займёт", "займет")

# Рубрика сайта -> тип для карточки
KIND_BY_CATEGORY = {
    "Сделка": "сделка",
    "Аукцион": "аукцион",
    "Инвестиции": "инвестиция",
    "Назначения": "назначение",
    "Исследования рынка": "аналитика",
    "Экспертный анализ": "аналитика",
    "Проект": "проект",
    "Открытие": "открытие",
    "Игроки рынка": "прочее",
}

# Куски названий мест. Ищем вхождение и возвращаем найденное словосочетание.
PLACE_STEMS = [
    "Москв", "Подмосков", "Новой Москв", "Петербург", "Ленинградск",
    "Зеленоград", "Химк", "Мытищ", "Домодедов", "Балаших", "Люберц",
    "Казан", "Екатеринбург", "Новосибирск", "Краснодар", "Сочи",
    "Ростов", "Самар", "Уф", "Челябинск", "Пермь", "Воронеж", "Тюмен",
    "Владивосток", "Хабаровск", "Калининград", "Нижн", "Алмат", "Астан",
    "Ташкент", "Дубай", "Минск",
]

DIRECTION_RE = re.compile(
    r"на\s+(север[еа]?|юг[еа]?|запад[еа]?|восток[еа]?|"
    r"северо-запад[еа]?|северо-восток[еа]?|юго-запад[еа]?|юго-восток[еа]?)"
    r"\s+([А-ЯЁ][а-яё]+[а-яё]*)",
    re.IGNORECASE,
)
METRO_RE = re.compile(r"у\s+(?:станции\s+)?метро\s+«?([А-ЯЁ][^»,.]{2,30})»?")
DISTRICT_RE = re.compile(r"в\s+([А-ЯЁ][а-яё]+ском)\s+районе")
# «в Сокольниках», «в «Южных Вратах»» — предложный падеж после «в»
FALLBACK_PLACE_RE = re.compile(
    r"\bв\s+«?([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]?[а-яё]+)?(?:ах|ях|е|и))»?\b"
)

AREA_RE = re.compile(
    r"(\d[\d\s.,]*)\s*(?:тыс\.?\s*)?кв\.?\s*м", re.IGNORECASE
)
# Деньги — только если рядом валюта или это «за N млрд» без «кв. м» следом
MONEY_RE = re.compile(
    r"(?:за\s+)?(\d[\d\s.,]*)\s*(млрд|млн|тыс\.?)\s*(руб\w*|₽|\$|долл\w*)?",
    re.IGNORECASE,
)

AD_MARKERS = ("реклама", "erid", "рекламодатель", "бронирование билетов")

QUOTES = " \t\u00a0«»\"'.,:;—–-"


def _find_verb(text: str, verbs: list[str]) -> tuple[str, int] | None:
    low = text.lower()
    for v in verbs:
        pos = low.find(v)
        if pos != -1:
            return v, pos
    return None


def _actor_before(text: str, pos: int) -> str | None:
    """Кто действует — обычно всё, что стоит до глагола."""
    part = text[:pos].strip(QUOTES)
    if not part or len(part) > 70:
        return None
    # отсекаем вводные вроде «СМИ сообщили:»
    part = re.sub(r"^(СМИ|Источник\w*|Эксперт\w*)[:,]\s*", "", part).strip(QUOTES)
    return part or None


def _object_after(text: str, pos: int, verb: str) -> str | None:
    part = text[pos + len(verb) :].strip(QUOTES)
    if not part:
        return None
    # обрезаем хвост про место и цену — они попадут в свои поля
    part = re.split(r"\s+(?:в|на|за|под|у)\s+[А-ЯЁ0-9]", part)[0]
    part = part.strip(QUOTES)
    return part[:90] or None


def _location(text: str) -> str | None:
    m = DIRECTION_RE.search(text)
    if m:
        return m.group(0)
    m = METRO_RE.search(text)
    if m:
        return f"метро {m.group(1).strip()}"
    m = DISTRICT_RE.search(text)
    if m:
        return m.group(0)
    for stem in PLACE_STEMS:
        idx = text.find(stem)
        if idx != -1:
            tail = text[idx:]
            word = re.match(r"[А-ЯЁа-яё\-]+", tail)
            return word.group(0) if word else stem
    # запасной вариант: «в Сокольниках», «в «Южных Вратах»»
    m = FALLBACK_PLACE_RE.search(text)
    if m:
        return _clean(m.group(1))
    return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,.")


def _amount(text: str) -> str | None:
    """Сумма и площадь — оба полезны, показываем что нашли."""
    parts = []

    for m in MONEY_RE.finditer(text):
        tail = text[m.end() : m.end() + 8].lower()
        if "кв" in tail:  # это площадь, а не деньги
            continue
        if not m.group(3) and "за" not in m.group(0).lower():
            continue  # ни валюты, ни предлога «за» — скорее всего не цена
        parts.append(_clean(m.group(0).replace("за ", "")))
        break

    m = AREA_RE.search(text)
    if m:
        parts.append(_clean(m.group(0)))

    return " · ".join(parts) if parts else None


def extract(title: str, text: str, category: str | None) -> dict:
    """Возвращает тот же словарь, что и разбор через ИИ."""
    haystack = f"{title}\n{text[:600]}"
    low = haystack.lower()

    if any(marker in low for marker in AD_MARKERS) and not category:
        return {"relevant": False, "kind": "прочее"}

    kind = KIND_BY_CATEGORY.get(category or "", "прочее")

    buyer = seller = obj = None

    # безличные обороты разбираем первыми: стороны там не названы
    hit = _find_verb(title, PASSIVE_VERBS)
    if hit:
        verb, pos = hit
        obj = _actor_before(title, pos)
        return {
            "relevant": True,
            "kind": "аукцион"
            if any(w in verb for w in ("аукцион", "торги", "молотка"))
            else kind,
            "buyer": None,
            "seller": None,
            "location": _location(haystack),
            "object": obj,
            "amount": _amount(haystack),
            "stage": "выставлено на торги",
            "summary": None,
        }

    hit = _find_verb(title, BUY_VERBS)
    if hit:
        verb, pos = hit
        buyer = _actor_before(title, pos)
        obj = _object_after(title, pos, verb)
        if any(m in verb for m in RENT_MARKERS):
            kind = "аренда"

    hit = _find_verb(title, SELL_VERBS)
    if hit:
        verb, pos = hit
        seller = _actor_before(title, pos)
        if obj is None:
            obj = _object_after(title, pos, verb)

    # Если в заголовке есть действие со сторонами — это сделка, что бы ни
    # говорила рубрика источника. Рубрики на сайте размечены ненадёжно.
    if (buyer or seller) and kind in ("прочее", "аналитика", "назначение"):
        kind = "аренда" if _find_verb(title, ["аренд", "снял"]) else "сделка"

    return {
        "relevant": True,
        "kind": kind,
        "buyer": buyer,
        "seller": seller,
        "location": _location(haystack),
        "object": obj,
        "amount": _amount(haystack),
        "stage": None,
        "summary": None,
    }
