"""Разбор через GigaChat (Сбер).

Схема авторизации двухступенчатая: ключ авторизации из личного кабинета
меняется на access-токен, который живёт 30 минут, и уже им подписываются
запросы к модели. Токен здесь кэшируется и обновляется сам.
"""

import logging
import time
import uuid

import requests

import config

log = logging.getLogger("gigachat")

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_token: str | None = None
_token_expires: float = 0.0


class GigaChatError(RuntimeError):
    pass


def _verify():
    """Сбер использует сертификаты Минцифры.

    Если они не установлены в системе, запрос упадёт на проверке TLS.
    Правильное решение — поставить сертификаты. Отключение проверки
    (GIGACHAT_VERIFY_SSL=0) оставлено как временный костыль: соединение
    при этом перестаёт быть защищённым от подмены.
    """
    return config.GIGACHAT_VERIFY_SSL


def _fetch_token() -> str:
    global _token, _token_expires
    if _token and time.time() < _token_expires - 60:
        return _token

    if not config.GIGACHAT_AUTH_KEY:
        raise GigaChatError("Не заполнен GIGACHAT_AUTH_KEY в .env")

    resp = requests.post(
        OAUTH_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {config.GIGACHAT_AUTH_KEY}",
        },
        data={"scope": config.GIGACHAT_SCOPE},
        timeout=30,
        verify=_verify(),
    )
    if resp.status_code != 200:
        raise GigaChatError(f"Не выдан токен ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    _token = data.get("access_token")
    if not _token:
        raise GigaChatError("В ответе нет access_token")

    # expires_at приходит в миллисекундах; если его нет — считаем 30 минут
    expires_at = data.get("expires_at")
    _token_expires = expires_at / 1000 if expires_at else time.time() + 1800
    return _token


def complete(system: str, user: str) -> str:
    token = _fetch_token()
    resp = requests.post(
        CHAT_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={
            "model": config.GIGACHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        },
        timeout=60,
        verify=_verify(),
    )
    if resp.status_code == 401:
        # токен протух раньше времени — сбрасываем и пробуем ещё раз
        global _token
        _token = None
        token = _fetch_token()
        resp = requests.post(
            CHAT_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={
                "model": config.GIGACHAT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
            },
            timeout=60,
            verify=_verify(),
        )
    if resp.status_code != 200:
        raise GigaChatError(f"Ошибка модели ({resp.status_code}): {resp.text[:200]}")

    return resp.json()["choices"][0]["message"]["content"]


def check() -> str:
    """Быстрая проверка связи — для команды в боте."""
    try:
        answer = complete("Отвечай одним словом.", "Скажи: готово")
        return f"GigaChat отвечает: {answer.strip()[:60]}"
    except Exception as e:
        return f"GigaChat недоступен: {e}"
