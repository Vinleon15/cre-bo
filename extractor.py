"""Разбор материала. Режим выбирается на лету и переживает перезапуск.

Три варианта: правила (бесплатно), GigaChat, Claude. Промпт для обеих
моделей общий, отличается только транспорт.
"""

import asyncio
import json
import logging
import os
import time

import config
import db
import objects
import offline
import priority

log = logging.getLogger("extractor")

# Пока идёт разбор, рядом с кодом лежит отметка. Автообновление её видит
# и откладывает перезапуск: иначе выкатка посреди /reparse обрывала разбор
# на полпути, а материалы оставались со сброшенным статусом — сводка
# выглядела опустевшей.
BUSY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".busy")


def _busy_on() -> None:
    try:
        with open(BUSY_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        log.warning("Не удалось поставить отметку занятости")


def is_busy() -> bool:
    """Идёт ли разбор прямо сейчас.

    Отметку старше получаса считаем брошенной после сбоя — иначе одна
    неудача заблокировала бы команды навсегда. Тот же срок, что и в
    update.sh, и по той же причине.
    """
    try:
        age = time.time() - os.path.getmtime(BUSY_FILE)
    except OSError:
        return False
    return age < 1800


def _busy_off() -> None:
    try:
        os.remove(BUSY_FILE)
    except OSError:
        pass

SYSTEM = """Ты аналитик рынка коммерческой недвижимости. На вход — новость.

Верни СТРОГО один JSON-объект, без markdown и пояснений:
{
  "relevant": true | false,
  "kind": "сделка" | "аренда" | "аукцион" | "инвестиция" | "старт продаж"
          | "тэп" | "рнс" | "рнв" | "банкротство" | "суд" | "назначение"
          | "аналитика" | "прочее",
  "done": true | false,
  "buyer":    строка или null,
  "seller":   строка или null,
  "location": строка или null,
  "district":  строка или null,
  "segment":   строка или null,
  "obj_class": строка или null,
  "object":   строка или null,
  "amount":   строка или null,
  "area":     строка или null,
  "stage":    строка или null,
  "summary":  строка
}

Правила:
- relevant = false только для рекламы, анонсов мероприятий и промо изданий.
  Новости, аналитика и назначения — relevant = true с подходящим kind.
- kind — тип события, выбирай точнее. ГЛАВНОЕ ПРАВИЛО: сделка, аренда,
  аукцион и инвестиция ставятся только тогда, когда СОБЫТИЕ ПРОИЗОШЛО
  С КОНКРЕТНЫМ ОБЪЕКТОМ ИЛИ КОМПАНИЕЙ. Если объекта нет, это не сделка:
    «доля аукционов в инвестициях достигла 30%» — аналитика, не аукцион
    «фонд увеличил выплаты пайщикам в 1,8 раза» — аналитика, не сделка
    «ставки капитализации достигли 9,5–13,5%» — аналитика
    «компания выступила консультантом по аренде» — аналитика: работу
      сделал брокер, объект собственника не сменил
    «БЦ вошёл в категорию Prime», «получил премию» — аналитика
  Число в заголовке само по себе сделкой ничего не делает.
  сделка — смена собственника объекта или компании
  аренда — КРУПНАЯ аренда (от значимой площади). Аренда нескольких сотен
           кв. м под небольшой офис — тоже "аренда", это нормально,
           не выдумывай другой kind ради важности
  аукцион — торги, конкурс на право покупки/аренды
  инвестиция — вход в капитал, инвестиционная сделка без смены контроля
  старт продаж — стартовали продажи в проекте (жильё, апартаменты)
  тэп — получены/утверждены технико-экономические показатели проекта
  рнс — получено разрешение на строительство
  рнв — получено разрешение на ввод объекта в эксплуатацию
  банкротство — банкротство компании, застройщика или структуры-владельца
  суд — судебный спор, арбитражное дело, иск, оспаривание
  назначение, аналитика, прочее — как раньше
- buyer — кто покупает, арендует, входит в проект или получает актив.
  seller — кто продаёт, выходит или передаёт актив.
- НЕ ВЫДУМЫВАЙ. Если сторона не названа — null. Стороны часто не раскрывают,
  и пустое поле лучше догадки. Если названа расплывчато («группа инвесторов,
  имена не раскрываются») — так и напиши.
- district — район или округ Москвы, если можно понять из текста или адреса:
  «Пресненский», «Хамовники», «ЦАО», «Москва-Сити», «Новая Москва».
  Не выдумывай по названию объекта — только если прямо следует из текста.
- segment — что за недвижимость: «жильё», «офисы», «склад», «торговля»,
  «гостиница», «земля», «апартаменты», «многофункциональный». Если неясно — null.
- obj_class — класс объекта, ТОЛЬКО если он назван в тексте.
  Для жилья: эконом, комфорт, бизнес, премиум, элит.
  Для офисов: Prime, A, B+, B, C. Для складов: A, B.
  НЕ ОПРЕДЕЛЯЙ КЛАСС САМ по цене или адресу — если в тексте его нет, ставь null.
- location — максимально конкретно, как в тексте: город, район, улица.
  Если сделка на уровне компании и объект не привязан к адресу — null.
- amount — ТОЛЬКО деньги: «4,2 млрд руб.», «533,6 млн ₽». Если суммы нет — null.
- area — ТОЛЬКО площадь или размер: «9 тыс. кв. м», «51 га». Не смешивай с amount.
- done — САМОЕ ВАЖНОЕ ПОЛЕ. true только если событие УЖЕ ПРОИЗОШЛО:
  сделка подписана и закрыта, ТЭП утверждены, РНС/РНВ выданы, компания
  официально признана банкротом. Глаголы прошедшего времени и факт,
  а не намерение: купил, продал, получил разрешение, стал банкротом.
  false, если событие ещё НЕ произошло: слух, переговоры, намерение,
  «выставлено на продажу», «объявлены торги», «планирует купить»,
  «подано заявление о банкротстве» (решения ещё нет), «готовится подать
  документы на РНВ». ОСОБЕННО ВНИМАТЕЛЬНО читай на отрицания: если
  сделка или торги НЕ СОСТОЯЛИСЬ, покупатель НЕ НАЙДЕН, переговоры
  СОРВАЛИСЬ или ПРИОСТАНОВЛЕНЫ — это done = false, а НЕ факт продажи.
  Не путай упоминание провалившейся попытки с её успехом.
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
    _busy_on()
    try:
        return await _process_pending(limit)
    finally:
        _busy_off()


async def _process_pending(limit: int = 60) -> tuple[int, str | None]:
    mode = current_mode()

    if mode == "free":
        found = 0
        for row in db.pending(limit):
            data = offline.extract(row["title"], row["text"], row["category"])
            if data.get("relevant"):
                data = priority.finalize(data, row["title"], row["text"] or "")
            db.save_extraction(row["id"], data)
            if data.get("relevant"):
                found += 1
        created, attached = objects.process_new()
        log.info("Разобрано правилами: %s | объектов: +%s новых, "
                 "%s к существующим", found, created, attached)
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

        if data.get("relevant"):
            data = priority.finalize(data, row["title"], row["text"] or "")

        db.save_extraction(row["id"], data)
        errors = 0  # успех — счётчик подряд идущих сбоев обнуляем
        if data.get("relevant"):
            found += 1

    created, attached = objects.process_new()
    log.info("Распознано через %s: %s | объектов: +%s новых, "
             "%s к существующим", mode, found, created, attached)
    return found, last_error
