"""Адрес → координаты. Клиент геокодера с кэшем.

Место у площадки одно, как бы её ни называли: «около гостиницы Украина»,
«Кутузовский, 2/1» и «Украина-плаза» — одна точка на карте. Поэтому
площадки узнаются по расстоянию, а не по совпадению слов.

Координаты у модели не спрашиваются никогда: числа она выдаёт
правдоподобные и неверные. Дело модели — узнать место и назвать адрес,
дело геокодера — превратить адрес в точку.

Ответы кэшируются в базе, включая пустые: ненайденный адрес не стоит
спрашивать заново при каждом разборе.
"""

import logging
import math
import time
from datetime import datetime, timezone

import requests

import config
import db

log = logging.getLogger("geo")

# Между запросами к геокодеру — пауза, как в gigachat.py: бесплатные
# пределы считаются по частоте, а не только по общему числу.
MIN_INTERVAL = 0.2
_last_call = 0.0

# Точность ответа, которой можно верить. «street» и хуже означают, что
# геокодер нашёл только улицу целиком — такая точка гуляет на сотни
# метров, и радиус привязки перестаёт что-либо значить.
GOOD_PRECISION = ("exact", "number", "near")

URL = "https://geocode-maps.yandex.ru/1.x/"


def _spent_today() -> int:
    """Сколько обращений к геокодеру сделано сегодня.

    Бесплатный предел считается за сутки, и он невелик. Без своего счёта
    первый же полный переразбор сожжёт его на сотне материалов, а
    остальные адреса получат отказ — и, что хуже, запомнятся как
    ненайденные.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    if db.get_setting("geo_day") != today:
        return 0
    try:
        return int(db.get_setting("geo_spent") or 0)
    except ValueError:
        return 0


def _count_call() -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if db.get_setting("geo_day") != today:
        db.set_setting("geo_day", today)
        db.set_setting("geo_spent", "0")
    db.set_setting("geo_spent", str(_spent_today() + 1))


def budget_left() -> int:
    return max(config.GEO_DAILY_LIMIT - _spent_today(), 0)


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _ask(address: str) -> tuple[float | None, float | None, str | None]:
    """Спрашиваем геокодер. Ошибка сети — не повод падать: вернём пустое
    и попробуем в следующий раз, привязка по словам всё равно осталась."""
    _throttle()
    try:
        resp = requests.get(
            URL,
            params={"apikey": config.GEO_KEY, "geocode": address,
                    "format": "json", "results": 1, "lang": "ru_RU"},
            timeout=15,
        )
        resp.raise_for_status()
        found = (resp.json()["response"]["GeoObjectCollection"]
                 ["featureMember"])
    except Exception as e:
        log.warning("Геокодер не ответил по адресу %r: %s", address, e)
        return None, None, None

    if not found:
        return None, None, "not found"

    obj = found[0]["GeoObject"]
    precision = (obj.get("metaDataProperty", {})
                 .get("GeocoderMetaData", {}).get("precision"))
    # Яндекс отдаёт «долгота широта» через пробел — порядок обратный
    # привычному, перепутать легко.
    lon, lat = (float(x) for x in obj["Point"]["pos"].split())
    return lat, lon, precision


def point(address: str | None) -> tuple[float, float] | None:
    """Координаты адреса или None. Сначала кэш, потом геокодер."""
    if not address or not config.GEO_KEY:
        return None
    address = address.strip()
    if len(address) < 6:
        return None

    row = db.geocache_get(address)
    if row is None:
        if not budget_left():
            log.info("Дневной предел геокодера исчерпан, адрес %r отложен",
                     address)
            return None
        _count_call()
        lat, lon, precision = _ask(address)
        # Запоминаем только настоящий ответ. «Геокодер не ответил» — это
        # сеть или исчерпанный предел, а не свойство адреса: запиши мы
        # такое в кэш, адрес больше никогда бы не переспросили.
        if precision is not None:
            db.geocache_put(address, lat, lon, precision)
    else:
        lat, lon, precision = row["lat"], row["lon"], row["precision"]

    if lat is None or precision not in GOOD_PRECISION:
        return None
    return lat, lon


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Расстояние между точками в метрах."""
    lat1, lon1 = (math.radians(x) for x in a)
    lat2, lon2 = (math.radians(x) for x in b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(h))
