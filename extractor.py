"""Разбор материала. Режим выбирается на лету и переживает перезапуск.

Три варианта: правила (бесплатно), GigaChat, Claude. Промпт для обеих
моделей общий, отличается только транспорт.
"""

import asyncio
import json
import logging

import config
import db
import offline

log = logging.getLogger("extractor")

SYSTEM = """Ты аналитик рынка коммерческой недвижимости. На вход — новость.

Верни СТРОГО один JSON-объект, без markdown и пояснений:
{
  "relevant": true | false,
  "kind": "сделка" | "аренда" | "аукцион" | "инвестиция" | "назначение"
          | "аналитика" | "прочее",
  "buyer":    строка или null,
  "seller":   строка или null,
  "location": строка или null,
  "object":   строка или null,
  "amount":   строка или null,
  "stage":    строка или null,
  "summary":  строка
}

Правила:
- relevant = false только для рекламы, анонсов мероприятий и промо изданий.
  Новости, аналитика и назначения — relevant = true с подходящим kind.
- buyer — кто покупает, арендует, входит в проект или получает актив.
  seller — кто продаёт, выходит или передаёт актив.
- НЕ ВЫДУМЫВАЙ. Если сторона не названа — null. Стороны часто не раскрывают,
  и пустое поле лучше догадки. Если названа расплывчато («группа инвесторов,
  имена не раскрываются») — так и напиши.
- location — максимально конкретно, как в тексте: город, район, улица.
  Если сделка на уровне компании и объект не привязан к адресу — null.
- amount — сумма или площадь дословно, как указано.
- stage — «слух», «переговоры», «сделка закрыта», «выставлено на продажу».
- summary — суть своими словами, одно предложение до 20 слов. Не копируй
  формулировки исходного текста дословно, перескажи.
- Все значения на русском."""


def current_mode() -> str:
    """Режим из настроек бота, а если там пусто — из .env."""
    return db.get_setting("mode", config.MODE) or config.MODE


def set_mode(mode: str) -> None:
    db.set_setting("mode", mode)


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def _prompt(title: str, text: str, category: str | None) -> str:
    hint = f"Рубрика источника: {category}\n\n" if category else ""
    return f"{hint}{title}\n\n{text}"[:8000]


async def _extract_claude(title, text, category) -> dict | None:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=config.MODEL,
        max_tokens=700,
        system=SYSTEM,
        messages=[{"role": "user", "content": _prompt(title, text, category)}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(raw)


async def _extract_gigachat(title, text, category) -> dict | None:
    import gigachat

    raw = await asyncio.to_thread(
        gigachat.complete, SYSTEM, _prompt(title, text, category)
    )
    return _parse_json(raw)


async def process_pending(limit: int = 60) -> tuple[int, str | None]:
    """Разбирает накопившееся. Возвращает (сколько распознано, текст ошибки)."""
    mode = current_mode()

    if mode == "free":
        found = 0
        for row in db.pending(limit):
            data = offline.extract(row["title"], row["text"], row["category"])
            db.save_extraction(row["id"], data)
            if data.get("relevant"):
                found += 1
        log.info("Разобрано правилами: %s", found)
        return found, None

    handler = _extract_claude if mode == "claude" else _extract_gigachat
    found, errors, last_error = 0, 0, None

    for row in db.pending(limit):
        try:
            data = await handler(row["title"], row["text"], row["category"])
        except Exception as e:
            last_error = str(e)[:200]
            errors += 1
            db.mark_error(row["id"])
            log.warning("Разбор материала %s: %s", row["id"], e)
            if errors >= 3:  # что-то системно не так — не мучаем сервис
                return found, f"Остановлено после трёх ошибок. {last_error}"
            await asyncio.sleep(1)
            continue

        if data is None:
            db.mark_error(row["id"])
            continue

        db.save_extraction(row["id"], data)
        if data.get("relevant"):
            found += 1

    log.info("Распознано через %s: %s", mode, found)
    return found, last_error
