"""Расстановка cache_control и защита префикса от молчаливой инвалидации.

Кэш Anthropic работает по совпадению ПРЕФИКСА. Порядок рендера запроса:
tools -> system -> messages. Любое изменение байта в префиксе обесценивает
всё, что после него. Отсюда два правила:
  1) блоки идут от самых стабильных к самым изменчивым;
  2) в стабильной части не должно быть текущего времени, UUID и прочего,
     что меняется каждый запрос.
"""

from __future__ import annotations

import re

EPHEMERAL = {"type": "ephemeral"}
MAX_BREAKPOINTS = 4
# Минимальный кэшируемый префикс зависит от модели (512-4096 токенов).
# Берём консервативные 1024: более короткий префикс молча не закэшируется.
MIN_CACHEABLE = 1024

# Паттерны, которые убивают кэш, если попали в стабильную часть промпта.
INVALIDATORS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"), "временная метка"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "UUID"),
    (re.compile(r"\b\d{10,13}\b"), "unix-время или счётчик"),
    (re.compile(r"0x[0-9a-f]{6,}", re.I), "адрес объекта в памяти"),
]


def est_tokens(text: str) -> int:
    """Грубая оценка для внутренних решений (кириллица ~3 симв/токен).
    Точный счёт — только через client.messages.count_tokens."""
    return max(1, len(text) // 3)


def audit(text: str) -> list[str]:
    """Что в этом блоке помешает кэшированию."""
    return [name for pattern, name in INVALIDATORS if pattern.search(text)]


def text_block(text: str, cached: bool = False, ttl_1h: bool = False) -> dict:
    block = {"type": "text", "text": text}
    if cached:
        block["cache_control"] = dict(EPHEMERAL, ttl="1h") if ttl_1h else EPHEMERAL
    return block


def build_system(parts: list[tuple[str, str]], ttl_1h: bool = False) -> tuple[list[dict], list[str]]:
    """parts — [(имя, текст)] в порядке убывания стабильности.

    Брейкпоинт ставится на границе блока, только если накопленный префикс
    уже достаточно велик, чтобы вообще закэшироваться. Возвращает блоки и
    список предупреждений аудита.
    """
    blocks: list[dict] = []
    warnings: list[str] = []
    running = 0
    used = 0

    for i, (name, text) in enumerate(parts):
        if not text:
            continue
        for problem in audit(text):
            warnings.append(f"блок '{name}': {problem} в кэшируемой части — кэш будет промахиваться")
        running += est_tokens(text)
        is_last = i == len(parts) - 1
        # Кэшируем границу блока, если префикс дорос и брейкпоинты не исчерпаны.
        # Последний блок брейкпоинтом не закрываем: за ним идут messages,
        # и брейкпоинт там полезнее (см. mark_history).
        cached = running >= MIN_CACHEABLE and used < MAX_BREAKPOINTS - 1 and not is_last
        if cached:
            used += 1
        blocks.append(text_block(text, cached=cached, ttl_1h=ttl_1h))

    return blocks, warnings


def mark_history(messages: list[dict], ttl_1h: bool = False) -> list[dict]:
    """Брейкпоинт на последнем ходе ИСТОРИИ (не на новом вопросе).

    Так закэшированной оказывается вся история, а изменчивый вопрос остаётся
    за границей кэша. Ставить брейкпоинт на сам вопрос бессмысленно — он
    каждый раз другой.
    """
    if len(messages) < 2:
        return messages

    out = [dict(m) for m in messages]
    target = out[-2]
    content = target["content"]
    if isinstance(content, str):
        target["content"] = [text_block(content, cached=True, ttl_1h=ttl_1h)]
    elif isinstance(content, list) and content:
        blocks = [dict(b) for b in content]
        blocks[-1]["cache_control"] = dict(EPHEMERAL, ttl="1h") if ttl_1h else EPHEMERAL
        target["content"] = blocks
    return out


def check_hits(usage, request_index: int) -> str | None:
    """Диагностика после ответа. Ноль чтений кэша на повторных запросах —
    верный признак, что префикс нестабилен."""
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if request_index > 0 and read == 0 and written > 0:
        return ("кэш пишется, но не читается — префикс меняется между запросами. "
                "Проверьте system-блоки на время/UUID/несортированный JSON.")
    return None
