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
# Двух не хватало: совпадения вроде «покупатель + сумма» встречаются
# у разных сделок одного игрока и склеивали их в одну карточку.
MIN_COMMON = 3

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


def _same_deal(a_key: set[str], b_key: set[str]) -> bool:
    return len(a_key & b_key) >= MIN_COMMON


def group_by_object(rows: list) -> tuple[list[list], list]:
    """Сначала группируем по постоянному id объекта.

    Это надёжнее сравнения слов: связь уже установлена при разборе
    и учитывает всю накопленную историю, а не только текущую выборку.
    """
    buckets: dict[int, list] = {}
    rest: list = []
    for row in rows:
        oid = row["object_id"] if "object_id" in row.keys() else None
        if oid:
            buckets.setdefault(oid, []).append(row)
        else:
            rest.append(row)
    return list(buckets.values()), rest


def group(rows: list) -> list[list]:
    """Разбивает карточки на группы. Связь транзитивна: A~B, B~C → одна группа.

    Пример: «18% акций Самолета» и «акция компании Самолет» связаны словом
    «самоле», а «акции девелопера» цепляется к первой через числа 18 и 4.2 —
    хотя со второй напрямую общих слов не имеет.
    """
    keys = [_key(r) for r in rows]
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

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            gap = abs((_published(rows[i]) - _published(rows[j])).days)
            if gap <= MAX_GAP_DAYS and _same_deal(keys[i], keys[j]):
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
    by_object, rest = group_by_object(rows)
    # у кого объекта ещё нет — досклеиваем по словам, как раньше
    groups = by_object + group(rest)
    groups.sort(key=lambda g: max(_published(r) for r in g), reverse=True)
    return [combine(g) for g in groups]
