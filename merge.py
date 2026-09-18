"""Склейка карточек, описывающих одну и ту же сделку.

Одну новость публикуют несколько каналов, каждый своими словами. Сравнивать
заголовки бесполезно: «18% акций Самолета», «акция компании Самолет» и
«акции девелопера» — про одно событие.

Поэтому сравниваем не текст, а набор значимых слов из полей карточки.
Слова обрезаются до шести букв, чтобы «самолета» и «самолет» совпали.

Склейка не изменяет базу — считается на лету при сборке дайджеста.
Значит новая публикация о той же сделке подхватится сама.
"""

import re
from datetime import datetime

# Сколько общих слов достаточно, чтобы счесть карточки одной сделкой.
# Двух хватает, но только если среди совпавших есть опорное — из названия
# объекта или адреса. Без этого условия двойка ловит «покупатель + сумма»
# и склеивает разные сделки одного игрока. С тремя же переставали
# сходиться настоящие дубли: у пары «А22» и «СберСити» общих примет
# ровно две.
MIN_COMMON = 2

# Приметы, по которым узнаётся сама площадка, а не обстоятельства сделки.
# Тот же приём, что в objects.py: участники и сумма меняются от сделки к
# сделке, название и адрес — нет.
def _core(row) -> set[str]:
    return _stems(row["object"], row["location"])

# Карточки дальше друг от друга по времени не склеиваем: у одного покупателя
# может быть несколько разных сделок за год.
MAX_GAP_DAYS = 10

STEM_LEN = 6

# Слишком частые слова — по ним склеилось бы всё подряд
STOP = {
    "москва", "москве", "москвы", "россии", "рублей", "рубля", "рублях",
    "компан", "группа", "группы", "холдин", "девело", "девелоп", "гк",
    "ооо", "оао", "зао", "пао", "ук", "тыс", "млн", "млрд", "кв", "га",
    "около", "более", "менее", "новый", "новая", "проект", "объект",
    "здание", "участо", "помеще", "площад", "центр", "бизнес", "офисны",
    "офис", "фонд", "акции", "акций", "структ", "инвест",
    # Общие слова адреса. Без них «Дмитровское шоссе» и «Каширское шоссе»
    # роднятся по слову «шоссе», и две разные сделки сходятся в одну.
    "шоссе", "улица", "улице", "улицы", "проспе", "переул", "набере",
    "бульва", "район", "округ", "город", "корпус", "строен", "владен",
    "руб", "доллар", "евро",
}


def _stems(*values: str | None) -> set[str]:
    """Значимые основы слов и числа из переданных полей."""
    out: set[str] = set()
    for value in values:
        if not value:
            continue
        for word in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}|\d+[,.]?\d*", str(value)):
            low = word.lower()
            if low.replace(",", "").replace(".", "").isdigit():
                out.add(low.replace(",", "."))  # числа целиком: 18, 4.2
                continue
            stem = low[:STEM_LEN]
            if stem not in STOP and len(stem) >= 3:
                out.add(stem)
    return out


def _key(row) -> set[str]:
    return _stems(
        row["buyer"], row["seller"], row["object"], row["location"],
        row["amount"], row["area"],
    )


def _published(row) -> datetime:
    return datetime.fromisoformat(row["published"])


def _same_deal(a_key: set[str], b_key: set[str],
               a_core: set[str], b_core: set[str]) -> bool:
    common = a_key & b_key
    if not common & (a_core | b_core):
        return False  # сошлось только на сумме или участниках — не довод
    if len(common) >= MIN_COMMON:
        return True
    # Карточка может быть описана предельно скупо — «БЦ «Фрейм»» и всё.
    # Двух совпадений она не наберёт никогда, но если все её приметы есть
    # у другой карточки, это она же, только пересказанная короче.
    small = a_key if len(a_key) <= len(b_key) else b_key
    return len(small) <= 2 and common == small


def group(rows: list, linked: list | None = None) -> list[list]:
    """Разбивает карточки на группы. Связь транзитивна: A~B, B~C → одна группа.

    Пример: «18% акций Самолета» и «акция компании Самолет» связаны словом
    «самоле», а «акции девелопера» цепляется к первой через числа 18 и 4.2 —
    хотя со второй напрямую общих слов не имеет.
    """
    keys = [_key(r) for r in rows]
    cores = [_core(r) for r in rows]
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    # Сначала — связи, установленные при разборе: одинаковый объект.
    # Это сильная связь, на неё ограничение по времени не распространяется.
    for pair in (linked or []):
        union(*pair)

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            gap = abs((_published(rows[i]) - _published(rows[j])).days)
            if gap <= MAX_GAP_DAYS and _same_deal(keys[i], keys[j],
                                                  cores[i], cores[j]):
                union(i, j)

    buckets: dict[int, list] = {}
    for idx, row in enumerate(rows):
        buckets.setdefault(find(idx), []).append(row)

    # порядок групп — по самой свежей карточке внутри
    return sorted(
        buckets.values(),
        key=lambda g: max(_published(r) for r in g),
        reverse=True,
    )


_PRIORITY_ORDER = {"высокий": 0, "средний": 1, "низкий": 2}


def _max_priority(values: list) -> str | None:
    """Если хоть один источник расценил как высокий приоритет — оставляем
    высокий, даже когда другой источник того же события мягче."""
    valid = [v for v in values if v in _PRIORITY_ORDER]
    return min(valid, key=lambda v: _PRIORITY_ORDER[v]) if valid else None


def _best(values: list[str]) -> str | None:
    """Самое частое значение, при равенстве — самое подробное."""
    values = [v.strip() for v in values if v and v.strip()]
    if not values:
        return None
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda v: (counts[v], len(v)))


def _variants(values: list[str], limit: int = 2) -> str | None:
    """Если источники называют сторону по-разному — показываем оба варианта.

    Это не ошибка разбора: разные издания называют разные звенья цепочки
    владения, и обе версии информативны.
    """
    values = [v.strip() for v in values if v and v.strip()]
    if not values:
        return None

    unique: list[str] = []
    for v in values:
        low = v.lower()
        # похожие формулировки не дублируем
        if any(low in u.lower() or u.lower() in low for u in unique):
            continue
        unique.append(v)

    unique.sort(key=len, reverse=True)
    return " / ".join(unique[:limit])


def _completeness(row) -> int:
    """Сколько полей заполнено — мера подробности карточки."""
    fields = ("buyer", "seller", "location", "amount", "area", "stage", "summary")
    return sum(1 for f in fields if row[f])


def combine(rows: list) -> dict:
    """Собирает одну карточку из группы. Поля берутся лучшие по каждому."""
    pick = lambda f: _best([r[f] for r in rows])

    # Название объекта и суть берём из самой подробной карточки, а не самой
    # длинной: источник, заполнивший больше полей, обычно и описал точнее.
    richest = max(rows, key=_completeness)

    keys = rows[0].keys()
    get = lambda r, f: (r[f] if f in keys else None)

    return {
        "object_id": get(richest, "object_id"),
        "district": _best([get(r, "district") for r in rows]),
        "okrug": _best([get(r, "okrug") for r in rows]),
        "segment": _best([get(r, "segment") for r in rows]),
        "obj_class": _best([get(r, "obj_class") for r in rows]),
        "priority": _max_priority([get(r, "priority") for r in rows]),
        # если хоть один источник счёл событие московским — не прячем
        "is_moscow": any(bool(get(r, "is_moscow")) for r in rows),
        "object": richest["object"] or _best([r["object"] for r in rows]),
        "buyer": _variants([r["buyer"] for r in rows]),
        "seller": _variants([r["seller"] for r in rows]),
        "location": pick("location"),
        "amount": pick("amount"),
        "area": pick("area"),
        "stage": pick("stage"),
        "kind": pick("kind"),
        "summary": richest["summary"] or _best([r["summary"] for r in rows]),
        "published": max(r["published"] for r in rows),
        "sources": [(r["url"], r["source"]) for r in rows],
    }


def merged(rows: list) -> list[dict]:
    """Главная функция: из списка карточек — список объединённых."""
    # Раньше карточки с назначенным объектом раскладывались по объектам и
    # между собой не сравнивались вовсе — словесная склейка доставалась
    # только остатку. Пока объекты были «липкими», это сходило с рук.
    # После того как привязка к объектам стала строже, карточки одного
    # события разъехались по разным объектам, и склейка их не видела.
    # Теперь одинаковый объект — просто готовая связь, поверх которой
    # работает общее сравнение.
    by_oid: dict = {}
    linked: list = []
    for idx, row in enumerate(rows):
        oid = row["object_id"] if "object_id" in row.keys() else None
        if oid:
            if oid in by_oid:
                linked.append((by_oid[oid], idx))
            else:
                by_oid[oid] = idx

    groups = group(rows, linked)
    groups.sort(key=lambda g: max(_published(r) for r in g), reverse=True)
    return [combine(g) for g in groups]
