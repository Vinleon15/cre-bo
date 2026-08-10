import logging

import config
from sources import cre_site, tg_channel

log = logging.getLogger("sources")

COLLECTORS = {"site": cre_site.collect, "tg": tg_channel.collect}


def collect_all() -> int:
    total = 0
    for name in config.SOURCES:
        fn = COLLECTORS.get(name)
        if fn is None:
            log.warning("Неизвестный источник в SOURCES: %s", name)
            continue
        try:
            total += fn()
        except Exception:
            log.exception("Сбой источника %s", name)
    return total
