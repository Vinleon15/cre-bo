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
  "done": true | false,
  "buyer":    строка или null,
  "seller":   строка или null,
  "location": строка или null,
  "object":   строка или null,
  "amount":   строка или null,
  "area":     строка или null,
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
- amount — ТОЛЬКО деньги: «4,2 млрд руб.», «533,6 млн ₽». Если суммы нет — null.
- area — ТОЛЬКО площадь или размер: «9 тыс. кв. м», «51 га». Не смешивай с amount.
- done — САМОЕ ВАЖНОЕ ПОЛЕ. true только если сделка УЖЕ СОСТОЯЛАСЬ:
  подписана, закрыта, объект куплен, продан, арендован, торги выиграны,
  собственник сменился. Глаголы прошедшего времени: купил, продал,
  приобрёл, арендовал, закрыл сделку, стал владельцем.
  false, если сделка ещё НЕ состоялась: слух, переговоры, намерение,
  «выставлено на продажу», «объявлены торги», «планирует купить»,
  «торги не состоялись», «ищет покупателя», «может приобрести».
  Если из текста непонятно — ставь false.
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
            # Временные ограничения частоты не повод бросать всю пачку:
            # gigachat.py уже подождал и повторил. Считаем только подряд идущие
            # сбои и сдаёмся, если сервис отвечает ошибкой стабильно.
            if "429" in last_error or "Too Many" in last_error:
                await asyncio.sleep(5)
                continue
            if errors >= 5:
                return found, f"Остановлено после пяти ошибок подряд. {last_error}"
            await asyncio.sleep(2)
            continue

        if data is None:
            db.mark_error(row["id"])
            continue

        db.save_extraction(row["id"], data)
        errors = 0  # успех — счётчик подряд идущих сбоев обнуляем
        if data.get("relevant"):
            found += 1

    log.info("Распознано через %s: %s", mode, found)
    return found, last_error
