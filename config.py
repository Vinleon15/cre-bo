import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_IDS = {int(x) for x in _list("OWNER_IDS") if x.lstrip("-").isdigit()}

# free — правилами, бесплатно | gigachat — Сбер | claude — Anthropic
MODE = os.getenv("MODE", "free").strip().lower()
if MODE == "ai":       # старое название режима
    MODE = "claude"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODEL = os.getenv("MODEL", "claude-sonnet-5").strip()

GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat").strip()
GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "1").strip() not in ("0", "false")

MODE_NAMES = {
    "free": "Бесплатно (правилами)",
    "gigachat": "GigaChat",
    "claude": "Claude",
}

# Геокодер: адрес площадки превращается в точку, и площадки узнаются
# по расстоянию, а не по совпадению названий. Без ключа бот работает
# по-старому, привязкой по словам.
GEO_KEY = os.getenv("GEO_KEY", "").strip()
# Радиус, внутри которого считаем, что речь об одной площадке. Подбирать
# на реальных данных: больше — склеит соседние, меньше — разведёт одну.
GEO_RADIUS_M = _float("GEO_RADIUS_M", 150)

SOURCES = _list("SOURCES", "site")
CRE_CATEGORIES = [int(x) for x in _list("CRE_CATEGORIES", "13") if x.isdigit()]
TG_CHANNELS = [
    x.lstrip("@") for x in _list("TG_CHANNELS", os.getenv("TG_CHANNEL", "CRERussia"))
]

FETCH_FULL_TEXT = os.getenv("FETCH_FULL_TEXT", "1").strip() not in ("0", "false", "")
REQUEST_DELAY = max(1.0, _float("REQUEST_DELAY", 1.5))
PER_CATEGORY_LIMIT = _int("PER_CATEGORY_LIMIT", 20)

DB_PATH = os.getenv("DB_PATH", "data.sqlite3").strip()

# Прокси для выхода в интернет. Пусто — идём напрямую.
# Примеры: http://127.0.0.1:8080  |  socks5://127.0.0.1:1080
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# Фоновый сбор: раз в столько минут. 0 — выключить.
POLL_INTERVAL_MIN = _int("POLL_INTERVAL_MIN", 60)
# Час, когда присылать сводку за сутки (по времени сервера). Пусто — не слать.
_h = os.getenv("DAILY_DIGEST_HOUR", "").strip()
DAILY_DIGEST_HOUR = int(_h) if _h.isdigit() else None

USER_AGENT = "cre-digest-bot/1.0 (personal news digest; contact via Telegram)"

# Рубрики cre.ru — известные соответствия
CATEGORY_NAMES = {
    3: "Исследования рынка",
    5: "Экспертный анализ",
    6: "Инвестиции",
    7: "Назначения",
    9: "Власть",
    10: "Законодательство",
    12: "События",
    13: "Сделка",
    14: "Происшествие",
    22: "Проект",
    26: "Аукцион",
    27: "Конфликт",
    29: "Открытие",
    57: "Игроки рынка",
    62: "Переговоры",
    63: "Управление недвижимостью",
    98: "CRE на Движении",
}

# Рубрики, которые считаем сделочными — попадают в верхний блок дайджеста
DEAL_CATEGORIES = {13, 26, 6}


def validate() -> None:
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not OWNER_IDS:
        missing.append("OWNER_IDS")
    if MODE not in MODE_NAMES:
        missing.append("MODE (free, gigachat или claude)")
    if MODE == "claude" and not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY (нужен при MODE=claude)")
    if MODE == "gigachat" and not GIGACHAT_AUTH_KEY:
        missing.append("GIGACHAT_AUTH_KEY (нужен при MODE=gigachat)")
    if not SOURCES:
        missing.append("SOURCES")
    if missing:
        raise SystemExit("Не заполнено в .env: " + ", ".join(missing))
