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


# каналы, где окно превью могло переполниться между заходами
overflow: list[str] = []


def collect() -> int:
    total = 0
    overflow.clear()
    known = db.known_ext_ids("tg")
    for channel in config.TG_CHANNELS:
        try:
            total += _collect_one(channel, known)
        except Exception:
            log.exception("Канал %s: сбой", channel)
    log.info("telegram: новых постов %s из %s каналов",
             total, len(config.TG_CHANNELS))
    return total


def _collect_one(channel: str, known: set[str]) -> int:
    html = get(f"https://t.me/s/{channel}")
    if not html:
        log.warning("Канал %s недоступен — проверь имя и что он публичный", channel)
        return 0

    soup = BeautifulSoup(html, "lxml")
    added = 0
    seen_here = 0

    for block in soup.select("div.tgme_widget_message"):
        seen_here += 1
        post = block.get("data-post")  # вида "CRERussia/19695"
        if not post:
            continue
        ext_id = post  # вида "CRERussia/19695" — уникален между каналами
        if ext_id in known:
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
            ext_id=ext_id,
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
        known.add(ext_id)

    # Все посты на странице оказались новыми — значит окно, вероятно,
    # переполнилось, и что-то могло уйти за край. Не факт, но повод сказать.
    if seen_here and added == seen_here and known:
        overflow.append(channel)
        log.warning("%s: окно превью могло переполниться", channel)

    log.info("  %s: +%s", channel, added)
    return added
