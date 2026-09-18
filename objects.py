"""Постоянное отслеживание площадок.

Задача: понять, что новость касается площадки, о которой уже писали
раньше — хоть месяц назад, — и прицепить её к общей истории.

Отличие от merge.py: тот склеивает карточки внутри одной сводки, чтобы
не показывать одно и то же трижды. Здесь память постоянная, объект
живёт в базе и накапливает события.

Сопоставление — по набору значимых слов из полей карточки. Тот же приём,
что в merge.py, но порог выше: ошибка при склейке внутри сводки стоит
одной лишней строки, а здесь — испорченной истории объекта.
"""

import json
import re

import db
import refs

# Порог совпадения слов. Выше, чем в merge.py: за месяцы накапливается
# много объектов, и случайных пересечений становится больше.
MIN_COMMON = 3

# Сколько примет должно совпасть с ОПОРНЫМИ — именем площадки и адресом.
# Без этого условия объект превращался в магнит: приметы копились
# объединением при каждой новости, за сорок публикаций их набиралось
# столько, что подходило почти всё. Так банкротство совладельца
# «Большевика» слиплось с проектом на Варшавском шоссе. Опорные приметы
# задаются при создании объекта и больше не растут, поэтому площадка
# остаётся собой, сколько бы новостей о ней ни вышло.
MIN_CORE = 2

STEM_LEN = 6

# Частые слова, по которым склеилось бы всё подряд
STOP = {
    "москва", "москве", "москвы", "россии", "рублей", "рубля", "рублях",
    "компан", "группа", "группы", "холдин", "девело", "девелоп", "гк",
    "ооо", "оао", "зао", "пао", "ук", "тыс", "млн", "млрд", "кв", "га",
    "около", "более", "менее", "новый", "новая", "проект", "объект",
    "здание", "участо", "помеще", "площад", "центр", "бизнес", "офисны",
    "офис", "фонд", "акции", "акций", "структ", "инвест", "сделка",
    "сделки", "продаж", "покупк", "аренда", "аренды", "рынка", "рынке",
    # Общие слова адреса. Без них «Дмитровское шоссе» и «Каширское шоссе»
    # роднятся по слову «шоссе», и две разные сделки сходятся в одну.
    "шоссе", "улица", "улице", "улицы", "проспе", "переул", "набере",
    "бульва", "район", "округ", "город", "корпус", "строен", "владен",
    "руб", "доллар", "евро",
}


def stems(*values) -> set[str]:
    """Значимые основы слов и числа из переданных полей."""
    out: set[str] = set()
    for value in values:
        if not value:
            continue
        for word in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}|\d+[,.]?\d*", str(value)):
            low = word.lower().replace("ё", "е")
            if low.replace(",", "").replace(".", "").isdigit():
                out.add(low.replace(",", "."))
                continue
            stem = low[:STEM_LEN]
            if stem not in STOP and len(stem) >= 3:
                out.add(stem)
    return out


def item_stems(row) -> set[str]:
    """Ключ материала. Локация участвует, но не одна: иначе все сделки
    в одном районе слиплись бы в один объект."""
    return stems(row["object"], row["buyer"], row["seller"],
                 row["location"], row["area"])


def item_core(row) -> set[str]:
    """Опорные приметы: как площадка называется и где стоит.

    Покупатель, продавец и площадь сюда не входят — они меняются от
    сделки к сделке, а имя и адрес у площадки одни.
    """
    return stems(row["object"], row["location"], row["district"])


def item_fields(row) -> dict:
    district = row["district"]
    okrug = row["okrug"] or refs.okrug_by_district(district) \
        or refs.okrug_from_text(row["location"])
    return {
        "buyer": row["buyer"], "seller": row["seller"],
        "location": row["location"], "district": district, "okrug": okrug,
        "segment": row["segment"], "obj_class": row["obj_class"],
        "amount": row["amount"], "area": row["area"], "stage": row["stage"],
    }


def _load(obj, field: str) -> set[str]:
    try:
        return set(json.loads(obj[field] or "[]"))
    except (ValueError, TypeError, IndexError, KeyError):
        return set()


def find_match(key: set[str], core: set[str], objects: list) -> int | None:
    """Ищем объект с наибольшим пересечением, но не ниже обоих порогов.

    Должно совпасть и достаточно примет вообще, и достаточно опорных —
    имени площадки или адреса. Совпадения по одним лишь сопутствующим
    словам (сумма, участники, сегмент) объектом не считаются.
    """
    best_id, best_score = None, 0
    for obj in objects:
        obj_key = _load(obj, "stems")
        if not obj_key:
            continue
        obj_core = _load(obj, "core") or obj_key

        if len(core & obj_core) < MIN_CORE:
            continue
        score = len(key & obj_key)
        if score >= MIN_COMMON and score > best_score:
            best_id, best_score = obj["id"], score
    return best_id


def attach(row) -> tuple[int, bool]:
    """Привязывает материал к объекту. Возвращает (id объекта, новый ли)."""
    key = item_stems(row)
    core = item_core(row)
    if len(key) < MIN_COMMON or len(core) < MIN_CORE:
        # слишком мало примет — отдельный объект без шанса на склейку
        object_id = db.create_object(
            row["object"] or row["title"][:80], key,
            row["published"], item_fields(row), core)
        db.link_item(row["id"], object_id)
        return object_id, True

    objects = db.all_objects()
    match = find_match(key, core, objects)

    if match:
        db.update_object(match, key, row["published"], item_fields(row))
        db.link_item(row["id"], match)
        return match, False

    object_id = db.create_object(
        row["object"] or row["title"][:80], key,
        row["published"], item_fields(row), core)
    db.link_item(row["id"], object_id)
    return object_id, True


def process_new(limit: int = 500) -> tuple[int, int]:
    """Разносит по объектам всё разобранное, но ещё не привязанное.

    Возвращает (сколько создано новых объектов, сколько прицеплено
    к существующим).
    """
    created = attached = 0
    for row in db.unlinked_parsed(limit):
        _, is_new = attach(row)
        if is_new:
            created += 1
        else:
            attached += 1
    return created, attached
