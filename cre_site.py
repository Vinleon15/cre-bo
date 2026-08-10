"""Источник: сайт cre.ru.

Забираем ссылки со страниц рубрик, затем по каждой новости читаем страницу:
из meta-тегов достаются заголовок, дата и краткое описание, из тела —
полный текст. Если разметка сайта изменится, парсер тела деградирует
до заголовка с описанием, а они сами по себе дают часть полей карточки.
"""

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

import config
import db
from sources.base import Item, get

log = logging.getLogger("cre_site")

BASE = "https://cre.ru"
NEWS_RE = re.compile(r"^/(?:news|analytics)/(\d+)$")


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _parse_dt(raw: str | None) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def list_category(category_id: int, limit: int) -> list[tuple[str, str]]:
    """Возвращает [(news_id, url)] со страницы рубрики."""
    html = get(f"{BASE}/news/category/{category_id}")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if href.startswith(BASE):
            href = href[len(BASE) :]
        m = NEWS_RE.match(href)
        if not m:
            continue
        nid = m.group(1)
        if nid in seen:
            continue
        seen.add(nid)
        found.append((nid, BASE + href))
        if len(found) >= limit:
            break
    return found


def _body_text(soup: BeautifulSoup) -> str:
    """Тело статьи. Эвристика: самый насыщенный блок абзацев на странице."""
    best, best_len = "", 0
    for container in soup.find_all(["article", "div", "section"]):
        paragraphs = container.find_all("p", recursive=False)
        if len(paragraphs) < 2:
            continue
        text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs)
        if len(text) > best_len:
            best, best_len = text, len(text)
    if best_len < 120:  # разметка не опознана — берём все абзацы подряд
        chunks = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        chunks = [c for c in chunks if len(c) > 40]
        best = "\n".join(chunks[:15])
    return best.strip()


def fetch_article(news_id: str, url: str) -> Item | None:
    html = get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")

    title = _meta(soup, "og:title") or (soup.h1.get_text(strip=True) if soup.h1 else "")
    title = title.replace("\xa0", " ").strip()
    if not title:
        return None

    lead = (_meta(soup, "og:description") or "").replace("\xa0", " ").strip()
    published = _parse_dt(_meta(soup, "article:published_time"))

    # рубрика — по ссылке /category/<id> в тексте страницы
    category = None
    for a in soup.find_all("a", href=True):
        m = re.search(r"/category/(\d+)$", a["href"])
        if m:
            cid = int(m.group(1))
            label = a.get_text(strip=True)
            if label and cid in config.CATEGORY_NAMES:
                category = config.CATEGORY_NAMES[cid]
                break

    body = _body_text(soup) if config.FETCH_FULL_TEXT else ""
    text = "\n\n".join(x for x in (title, lead, body) if x)

    return Item(
        source="site",
        ext_id=news_id,
        url=url,
        title=title,
        text=text,
        published=published,
        category=category,
    )


def collect() -> int:
    known = db.known_ext_ids("site")
    added = 0
    for cid in config.CRE_CATEGORIES:
        for news_id, url in list_category(cid, config.PER_CATEGORY_LIMIT):
            if news_id in known:
                continue
            item = fetch_article(news_id, url)
            if item is None:
                continue
            if not item.category:
                item.category = config.CATEGORY_NAMES.get(cid)
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
            known.add(news_id)
    log.info("cre.ru: новых материалов %s", added)
    return added
