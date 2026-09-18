"""Общее для всех источников: вежливый HTTP и единый формат материала."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import requests

import config

log = logging.getLogger("sources")

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT})
if config.PROXY_URL:
    _session.proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL}
_last_request = 0.0


@dataclass
class Item:
    source: str
    ext_id: str
    url: str
    title: str
    text: str
    published: datetime
    category: str | None = None


def get(url: str, timeout: int = 20) -> str | None:
    """GET с обязательной паузой между запросами."""
    global _last_request
    wait = config.REQUEST_DELAY - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    try:
        resp = _session.get(url, timeout=timeout)
        _last_request = time.time()
        if resp.status_code != 200:
            log.warning("%s вернул %s", url, resp.status_code)
            return None
        # Кодировку берём из заголовка ответа. Автоопределение по тексту
        # ошибается на кириллице и превращает её в кракозябры.
        ctype = resp.headers.get("Content-Type", "").lower()
        if "charset=" in ctype:
            resp.encoding = ctype.split("charset=")[-1].split(";")[0].strip()
        else:
            resp.encoding = "utf-8"
        return resp.text
    except requests.RequestException as e:
        _last_request = time.time()
        log.warning("Не удалось загрузить %s: %s", url, e)
        return None
