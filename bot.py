"""Бот: собирает по запросу и отдаёт дайджест."""

import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart

import config
import db
import extractor
import merge
import sources

log = logging.getLogger("bot")

_session = None
if config.PROXY_URL:
    from aiogram.client.session.aiohttp import AiohttpSession

    _session = AiohttpSession(proxy=config.PROXY_URL)
    log.info("Telegram через прокси: %s", config.PROXY_URL)

bot = Bot(
    config.BOT_TOKEN,
    session=_session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

PERIODS = {"1": (1.0, "сутки"), "3": (3.0, "3 дня"), "7": (7.0, "неделю")}
KIND_ICONS = {
    "сделка": "🏢",
    "аренда": "🔑",
    "аукцион": "🔨",
    "инвестиция": "💼",
}


def _allowed(user_id: int) -> bool:
    """Чтение сводок. Открыто всем: бот не публикуется, имя знают свои."""
    return True


def _is_owner(user_id: int) -> bool:
    """Управление: сбор, переразбор, смена режима. Только владельцу.

    Отделено от чтения, потому что эти команды тратят токены модели
    и меняют содержимое базы для всех.
    """
    return user_id in config.OWNER_IDS


def _kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="Сутки", callback_data="d:1"),
                types.InlineKeyboardButton(text="3 дня", callback_data="d:3"),
                types.InlineKeyboardButton(text="Неделя", callback_data="d:7"),
            ]
        ]
    )


def esc(v) -> str:
    return html.escape(str(v)) if v else ""


def format_deal(card: dict) -> str:
    """Карточка сделки. На вход — объединённая запись из merge.combine."""
    icon = KIND_ICONS.get(card.get("kind") or "", "📌")
    dt = datetime.fromisoformat(card["published"]).strftime("%d.%m")

    lines = [f"{icon} <b>{esc(card.get('object')) or 'Сделка'}</b>"]
    if card.get("summary"):
        lines.append(f"<i>{esc(card['summary'])}</i>")
    lines.append(f"👤 Покупатель: {esc(card.get('buyer')) or '—'}")
    lines.append(f"🏷 Продавец: {esc(card.get('seller')) or '—'}")
    lines.append(f"📍 Локация: {esc(card.get('location')) or '—'}")
    if card.get("amount"):
        lines.append(f"💰 {esc(card['amount'])}")
    if card.get("area"):
        lines.append(f"📐 {esc(card['area'])}")
    if card.get("stage"):
        lines.append(f"📊 {esc(card['stage'])}")

    sources = card.get("sources") or []
    if len(sources) == 1:
        lines.append(f'🔗 <a href="{sources[0][0]}">Источник</a> · {dt}')
    else:
        links = " · ".join(
            f'<a href="{url}">{i}</a>' for i, (url, _) in enumerate(sources, 1)
        )
        lines.append(f"🔗 {len(sources)} источника: {links} · {dt}")

    return "\n".join(lines)


def build_digest(
    days: float,
    label: str,
    location: str | None = None,
    show_rest: bool = False,
) -> list[str]:
    """Три блока: состоявшиеся сделки, сделки в процессе и (по запросу) прочее."""
    # склейка считается на лету: новая публикация о той же сделке
    # подхватится в объединённую карточку автоматически
    done = merge.merged(db.deals(days, location))
    pending = db.in_progress(days, location)
    tail = db.rest(days) if show_rest else []

    if not done and not pending and not tail:
        return [f"За {label} ничего не найдено."]

    title = f"📊 <b>Дайджест за {label}</b> — сделок: {len(done)}"
    if location:
        title += f"\nФильтр по локации: {esc(location)}"

    chunks, cur = [], title

    def add(block: str):
        nonlocal cur
        if len(cur) + len(block) > 3800:
            chunks.append(cur)
            cur = block.strip()
        else:
            cur += block

    for row in done:
        add("\n\n" + format_deal(row))

    if pending:
        add(f"\n\n<b>В процессе ({len(pending)})</b>")
        for row in pending:
            stage = f" — {esc(row['stage'])}" if row["stage"] else ""
            add(f'\n• <a href="{row["url"]}">{esc(row["title"])[:80]}</a>{stage}')

    if tail:
        add(f"\n\n<b>Остальное ({len(tail)})</b>")
        for row in tail:
            add(f'\n• <a href="{row["url"]}">{esc(row["title"])[:90]}</a>')
    elif not show_rest:
        skipped = len(db.rest(days))
        if skipped:
            add(f"\n\n<i>Прочих материалов: {skipped}. Показать — /digest "
                f"{days:g} все</i>")

    chunks.append(cur)
    return chunks


async def send_digest(
    chat_id: int,
    days: float,
    label: str,
    location: str | None = None,
    show_rest: bool = False,
):
    for chunk in build_digest(days, label, location, show_rest):
        await bot.send_message(chat_id, chunk, disable_web_page_preview=True)


@dp.message(CommandStart())
async def cmd_start(msg: types.Message):
    if not _allowed(msg.from_user.id):
        return
    await msg.answer(
        "Собираю сделки с рынка коммерческой недвижимости.\n\n"
        "<b>Команды</b>\n"
        "/update — забрать свежее и разобрать\n"
        "/digest — сводка за сутки\n"
        "/digest 7 — за неделю\n"
        "/digest 3 Москва — за 3 дня с фильтром по локации\n"
        "/digest 7 все — добавить аналитику и назначения\n"
        "/mode — выбрать режим разбора\n"
        "/reparse — переразобрать всё заново\n"
        "/sources — сравнение полноты источников\n"
        "/status — что в базе",
        reply_markup=_kb(),
    )


@dp.message(Command("update"))
async def cmd_update(msg: types.Message):
    if not _is_owner(msg.from_user.id):
        return await msg.answer(
            "Эта команда доступна только владельцу бота.\n"
            "Сводка: /digest 7"
        )
    note = await msg.answer("Забираю свежее…")
    added = await asyncio.to_thread(sources.collect_all)
    await note.edit_text(f"Загружено новых материалов: {added}. Разбираю…")
    parsed, error = await extractor.process_pending()
    mode = config.MODE_NAMES.get(extractor.current_mode(), "?")
    msg = f"Готово. Новых материалов: {added}, распознано: {parsed}.\nРежим: {mode}"
    if error:
        msg += f"\n\n⚠️ {error}"
    await note.edit_text(msg)


@dp.message(Command("digest"))
async def cmd_digest(msg: types.Message):
    if not _allowed(msg.from_user.id):
        return
    parts = (msg.text or "").split()[1:]

    show_rest = False
    for word in ("все", "всё", "all"):
        if word in [p.lower() for p in parts]:
            show_rest = True
            parts = [p for p in parts if p.lower() != word]
            break

    days, location = 1.0, None
    if parts:
        try:
            days = float(parts[0].replace(",", "."))
            parts = parts[1:]
        except ValueError:
            pass
        if parts:
            location = " ".join(parts)

    await send_digest(msg.chat.id, days, f"{days:g} дн.", location, show_rest)


@dp.message(Command("sources"))
async def cmd_sources(msg: types.Message):
    if not _allowed(msg.from_user.id):
        return
    data = db.source_comparison(30)
    overlap = data.pop("_overlap", 0)
    if not data:
        return await msg.answer("Пока нечего сравнивать — запусти /update.")
    lines = ["<b>Полнота источников за 30 дней</b>", ""]
    for name, v in data.items():
        label = "сайт cre.ru" if name == "site" else "Telegram-канал"
        lines.append(f"{label}: уникальных {v['unique']}, повторов {v['dup']}")
    lines.append(f"\nПересечение источников: {overlap} материалов")
    lines.append(
        "\n<i>«Уникальных» — материал пришёл первым из этого источника. "
        "Если у канала эта цифра около нуля, он не добавляет ничего сверх сайта.</i>"
    )
    await msg.answer("\n".join(lines))


@dp.message(Command("status"))
async def cmd_status(msg: types.Message):
    if not _allowed(msg.from_user.id):
        return
    s = db.stats()
    st, src = s["status"], s["source"]
    await msg.answer(
        "📦 <b>База</b>\n"
        f"Распознано: {st.get('parsed', 0)}\n"
        f"Отсеяно как реклама: {st.get('skipped', 0)}\n"
        f"Ждут разбора: {st.get('new', 0)}\n"
        f"Дубли: {st.get('dup', 0)}\n"
        f"Ошибки: {st.get('error', 0)}\n\n"
        f"С сайта: {src.get('site', 0)} · из канала: {src.get('tg', 0)}\n"
        f"Режим разбора: {config.MODE_NAMES.get(extractor.current_mode(), '?')}"
    )


def _mode_kb() -> types.InlineKeyboardMarkup:
    cur = extractor.current_mode()
    rows = []
    for key, name in config.MODE_NAMES.items():
        mark = "✅ " if key == cur else ""
        rows.append(
            [types.InlineKeyboardButton(text=mark + name, callback_data=f"m:{key}")]
        )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("mode"))
async def cmd_mode(msg: types.Message):
    if not _is_owner(msg.from_user.id):
        return await msg.answer(
            "Эта команда доступна только владельцу бота.\n"
            "Сводка: /digest 7"
        )
    await msg.answer(
        "<b>Режим разбора</b>\n\n"
        "<b>Бесплатно</b> — правилами по заголовку. Ничего не стоит, "
        "работает мгновенно, но на непривычных формулировках "
        "оставляет поля пустыми.\n\n"
        "<b>GigaChat</b> — модель Сбера. Бесплатный лимит для физлиц, "
        "оплата российской картой. Нужен ключ в .env\n\n"
        "<b>Claude</b> — точнее всех, но нужен ключ Anthropic и баланс.\n\n"
        "После смены режима имеет смысл выполнить /reparse — "
        "уже собранное переразберётся заново.",
        reply_markup=_mode_kb(),
    )


@dp.message(Command("reparse"))
async def cmd_reparse(msg: types.Message):
    if not _is_owner(msg.from_user.id):
        return await msg.answer(
            "Эта команда доступна только владельцу бота.\n"
            "Сводка: /digest 7"
        )
    count = db.reset_parsed()
    if not count:
        return await msg.answer("Разбирать нечего — база пуста.")
    note = await msg.answer(f"Сбросил разбор у {count} материалов. Разбираю заново…")
    parsed, error = await extractor.process_pending(limit=count)
    text = f"Готово. Распознано: {parsed} из {count}."
    if error:
        text += f"\n\n⚠️ {error}"
    await note.edit_text(text)


@dp.message(Command("check"))
async def cmd_check(msg: types.Message):
    """Проверка связи с GigaChat — чтобы не гадать, в чём дело."""
    if not _is_owner(msg.from_user.id):
        return await msg.answer(
            "Эта команда доступна только владельцу бота.\n"
            "Сводка: /digest 7"
        )
    import gigachat

    note = await msg.answer("Проверяю связь…")
    result = await asyncio.to_thread(gigachat.check)
    await note.edit_text(result)


@dp.callback_query(F.data.startswith("m:"))
async def cb_mode(cq: types.CallbackQuery):
    if not _is_owner(cq.from_user.id):
        return await cq.answer("Только для владельца", show_alert=True)
    mode = cq.data.split(":")[1]
    if mode not in config.MODE_NAMES:
        return await cq.answer("Неизвестный режим")

    if mode == "claude" and not config.ANTHROPIC_API_KEY:
        return await cq.answer("Сначала впиши ANTHROPIC_API_KEY в .env", show_alert=True)
    if mode == "gigachat" and not config.GIGACHAT_AUTH_KEY:
        return await cq.answer("Сначала впиши GIGACHAT_AUTH_KEY в .env", show_alert=True)

    extractor.set_mode(mode)
    await cq.answer("Режим изменён")
    await cq.message.edit_text(
        f"Режим разбора: <b>{config.MODE_NAMES[mode]}</b>\n\n"
        "Дальше — /reparse, чтобы переразобрать уже собранное, "
        "или /update, если хочешь сначала забрать свежее.",
        reply_markup=_mode_kb(),
    )


@dp.callback_query(F.data.startswith("d:"))
async def cb_digest(cq: types.CallbackQuery):
    if not _allowed(cq.from_user.id):
        return await cq.answer()
    days, label = PERIODS[cq.data.split(":")[1]]
    await cq.answer("Собираю…")
    await send_digest(cq.message.chat.id, days, label)
