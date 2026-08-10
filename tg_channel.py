"""Источник: публичная веб-версия канала (t.me/s/<канал>).

Нужен для сравнения полноты с сайтом. Ограничения известны и намеренно
не обходятся: превью отдаёт одну страницу и может отставать от канала.
Личный аккаунт здесь не используется — только то, что Telegram публикует
для незалогиненных читателей.
"""

import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

import config
import db
from sources.base import Item, get

log = logging.getLogger("tg_channel")

MIN_LEN = 60


def _parse_dt(raw: str | None) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def collect() -> int:
    url = f"https://t.me/s/{config.TG_CHANNEL}"
    html = get(url)
    if not html:
        return 0

    soup = BeautifulSoup(html, "lxml")
    known = db.known_ext_ids("tg")
    added = 0

    for block in soup.select("div.tgme_widget_message"):
        post = block.get("data-post")  # вида "CRERussia/19695"
        if not post:
            continue
        msg_id = post.split("/")[-1]
        if msg_id in known:
            continue

        text_el = block.select_one("div.tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text("\n", strip=True)
        if len(text) < MIN_LEN:
            continue

        time_el = block.select_one("time")
        published = _parse_dt(time_el.get("datetime") if time_el else None)

        title = next((ln for ln in text.split("\n") if len(ln) > 15), text[:120])

        item = Item(
            source="tg",
            ext_id=msg_id,
            url=f"https://t.me/{post}",
            title=title.strip(),
            text=text,
            published=published,
            category=None,
        )
        if db.add_item(
            item.source,
            item.ext_id,
            item.url,
            item.title,
            item.text,
            item.published,
            item.category,
        ):
            added += 1
        known.add(msg_id)

    log.info("telegram: новых постов %s", added)
    return added
