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

# Вехи девелоперского проекта — узнаются по устойчивым формулировкам
# в новостях, без разбора подлежащего/сказуемого, в отличие от сделок.
MILESTONE_PHRASES = {
    "тэп": ("получил тэп", "получила тэп", "получены тэп", "утверждены тэп",
            "технико-экономические показатели"),
    "рнс": ("получил рнс", "получила рнс", "разрешение на строительство"),
    "рнв": ("получил рнв", "получила рнв", "разрешение на ввод",
            "ввод в эксплуатацию", "ввели в эксплуатацию", "ввела в эксплуатацию"),
    "старт продаж": ("старт продаж", "стартовали продажи", "начались продажи",
                     "открылись продажи", "старт реализации"),
    "банкротство": ("признан банкротом", "признана банкротом",
                    "введена процедура банкротства",
                    "начата процедура банкротства", "банкротство"),
    "суд": ("подал иск", "подала иск", "арбитражный суд", "судебный спор",
           "иск о взыскании", "оспорил", "оспорила", "оспаривает"),
    "крт": ("решение о крт", "проект крт", "пачка крт", "крт-нулевка",
            "комплексное развитие территории", "комплексного развития",
            "территория крт", "по программе крт"),
    "участок": ("на зу ", "земельный участок", "земельного участка",
                "земельному участку", "земельным участком",
                "земельные участки", "земельных участков",
                "земельными участками", "земельных участках", "участок под",
                "вид разрешённого использования", "вид разрешенного использования"),
    "выставление на продажу": ("выставил на продажу", "выставлен на продажу",
                               "выставлена на продажу", "выставили на продажу",
                               "ищет покупателя", "ищут покупателя",
                               "лот недели", "продаётся бц", "продается бц",
                               "выставил на торги", "выставлено на торги"),
}
# Суд обычно тянется, а не завершается одномоментно — done=false по умолчанию
# Решение о КРТ и событие по участку — свершившийся факт. Выставление на
# продажу — наоборот, только намерение: сделки ещё нет, ей место в разделе
# «в процессе», а не в карточках состоявшегося.
MILESTONE_DONE = {"тэп": True, "рнс": True, "рнв": True,
                  "старт продаж": True, "банкротство": True, "суд": False,
                  "крт": True, "участок": True,
                  "выставление на продажу": False}

QUOTES = " \t\u00a0\"'.,:;—–-"

# «у ВТБ», «у семьи Иванова» после глагола покупки — это продавец
FROM_WHOM_RE = re.compile(r"^\s*у\s+(«[^»]{2,50}»|[А-ЯЁ][^,]{1,45}?)(?=\s|$)")
# хвост про цену обрезаем: «за 13,5 млрд»
PRICE_TAIL_RE = re.compile(r"\s+за\s+\d.*$")


def _tidy(s: str) -> str:
    """Аккуратная обрезка: не разрывает кавычки и не глотает закрывающую."""
    s = s.strip(QUOTES)
    if s.count("«") > s.count("»"):
        s += "»"
    if s.count("»") > s.count("«"):
        s = "«" + s
    return s.strip()


def _find_verb(text: str, verbs: list[str]) -> tuple[str, int] | None:
    """Ищем самую длинную подходящую форму.

    Иначе «купил» сработает внутри «купила» и откусит от хвоста лишнюю букву.
    """
    low = text.lower()
    best: tuple[str, int] | None = None
    for v in verbs:
        pos = low.find(v)
        if pos == -1:
            continue
        if best is None or len(v) > len(best[0]):
            best = (v, pos)
    return best


def _actor_before(text: str, pos: int) -> str | None:
    """Кто действует — обычно всё, что стоит до глагола."""
    part = text[:pos].strip()
    if not part or len(part) > 70:
        return None
    part = re.sub(r"^(СМИ|Источник\w*|Эксперт\w*)[:,]\s*", "", part)
    return _tidy(part) or None


def _split_tail(text: str, pos: int, verb: str) -> tuple[str | None, str | None]:
    """Разбирает хвост после глагола на продавца («у кого») и объект."""
    tail = text[pos + len(verb) :]

    seller = None
    m = FROM_WHOM_RE.match(tail)
    if m:
        seller = _tidy(m.group(1))
        tail = tail[m.end() :]

    tail = PRICE_TAIL_RE.sub("", tail)
    obj = _tidy(tail)[:90]
    return seller, (obj or None)


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


def _area(text: str) -> str | None:
    m = AREA_RE.search(text)
    return _clean(m.group(0)) if m else None


def _amount(text: str) -> str | None:
    """Только деньги. Площадь возвращает _area отдельно."""
    for m in MONEY_RE.finditer(text):
        tail = text[m.end() : m.end() + 8].lower()
        if "кв" in tail:  # это площадь, а не деньги
            continue
        if not m.group(3) and "за" not in m.group(0).lower():
            continue  # ни валюты, ни предлога «за» — скорее всего не цена
        return _clean(m.group(0).replace("за ", ""))
    return None


def _milestone_kind(text: str) -> str | None:
    low = text.lower()
    for kind, phrases in MILESTONE_PHRASES.items():
        if any(p in low for p in phrases):
            return kind
    return None


def extract(title: str, text: str, category: str | None) -> dict:
    """Возвращает тот же словарь, что и разбор через ИИ."""
    haystack = f"{title}\n{text[:600]}"
    low = haystack.lower()

    if any(marker in low for marker in AD_MARKERS) and not category:
        return {"relevant": False, "kind": "прочее"}

    milestone = _milestone_kind(haystack)
    if milestone:
        return {
            "relevant": True,
            "kind": milestone,
            "done": MILESTONE_DONE[milestone],
            "object": _tidy(title[:90]),
            "location": _location(haystack),
            "amount": _amount(haystack),
            "area": _area(haystack),
        }

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
            "area": _area(haystack),
            "stage": "выставлено на торги",
            "done": False,   # объявлены торги — сделки ещё нет
            "summary": None,
        }

    hit = _find_verb(title, BUY_VERBS)
    if hit:
        verb, pos = hit
        buyer = _actor_before(title, pos)
        seller, obj = _split_tail(title, pos, verb)
        if any(m in verb for m in RENT_MARKERS):
            kind = "аренда"

    hit = _find_verb(title, SELL_VERBS)
    if hit:
        verb, pos = hit
        if not seller:
            seller = _actor_before(title, pos)
        if obj is None:
            _, obj = _split_tail(title, pos, verb)

    # Если в заголовке есть действие со сторонами — это сделка, что бы ни
    # говорила рубрика источника. Рубрики на сайте размечены ненадёжно.
    if (buyer or seller) and kind in ("прочее", "аналитика", "назначение"):
        kind = "аренда" if _find_verb(title, ["аренд", "снял"]) else "сделка"

    # Нашли сторону через глагол прошедшего времени — считаем состоявшейся.
    # Правила не различают оттенки, модель делает это точнее.
    done = bool(buyer or seller)

    return {
        "relevant": True,
        "kind": kind,
        "done": done,
        "buyer": buyer,
        "seller": seller,
        "location": _location(haystack),
        "object": obj,
        "amount": _amount(haystack),
        "area": _area(haystack),
        "stage": None,
        "summary": None,
    }
