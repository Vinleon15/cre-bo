"""SQLite: сырые материалы + результат разбора.

Ключевая деталь — дедупликация между источниками. Одна и та же новость
приходит с сайта полным текстом, а из Telegram — коротким анонсом, поэтому
хэшируем нормализованный ЗАГОЛОВОК, а не тело.
"""

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Что считаем значимым событием для карточек/«в процессе».
# Остальное (назначения, аналитика) — в хвост «Остальное».
DEAL_KINDS = (
    "сделка", "аренда", "аукцион", "инвестиция",
    "старт продаж", "тэп", "рнс", "рнв", "банкротство", "суд",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,             -- site | tg
    ext_id      TEXT NOT NULL,             -- id новости на сайте или id поста
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    title_hash  TEXT NOT NULL,
    text        TEXT NOT NULL,
    category    TEXT,
    published   TEXT NOT NULL,             -- ISO, UTC
    first_seen  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',   -- new|parsed|skipped|dup|error
    is_dup      INTEGER NOT NULL DEFAULT 0,
    buyer       TEXT,
    seller      TEXT,
    location    TEXT,
    object      TEXT,
    amount      TEXT,
    area        TEXT,
    stage       TEXT,
    kind        TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    object_id   INTEGER,
    district    TEXT,
    okrug       TEXT,
    segment     TEXT,
    obj_class   TEXT,   -- сделка состоялась, а не в процессе
    summary     TEXT,
    UNIQUE(source, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_items_pub    ON items(published);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_thash  ON items(title_hash);

CREATE TABLE IF NOT EXISTS objects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,          -- как называем площадку
    stems       TEXT NOT NULL,          -- ключевые слова, JSON-список
    core        TEXT,                   -- опорные приметы: имя и адрес,
                                        -- не растут со временем
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    events      INTEGER NOT NULL DEFAULT 0,
    -- лучшие известные сведения, накапливаются по мере новостей
    buyer       TEXT,
    seller      TEXT,
    location    TEXT,
    district    TEXT,
    okrug       TEXT,
    segment     TEXT,
    obj_class   TEXT,
    amount      TEXT,
    area        TEXT,
    stage       TEXT
);
CREATE INDEX IF NOT EXISTS idx_obj_last ON objects(last_seen);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init() -> None:
    global _conn
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.executescript(SCHEMA)
        # база могла быть создана до появления колонки done
        try:
            _conn.execute("ALTER TABLE objects ADD COLUMN core TEXT")
        except sqlite3.OperationalError:
            pass  # колонка уже есть

        for col, decl in (
            ("done", "INTEGER NOT NULL DEFAULT 0"), ("area", "TEXT"),
            ("object_id", "INTEGER"), ("district", "TEXT"), ("okrug", "TEXT"),
            ("segment", "TEXT"), ("obj_class", "TEXT"),
            ("priority", "TEXT"), ("is_moscow", "INTEGER NOT NULL DEFAULT 1"),
        ):
            try:
                _conn.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        _conn.commit()


def _title_hash(title: str) -> str:
    norm = re.sub(r"[^0-9a-zа-яё]+", "", title.lower())
    return hashlib.sha256(norm.encode()).hexdigest()


def add_item(
    source: str,
    ext_id: str,
    url: str,
    title: str,
    text: str,
    published: datetime,
    category: str | None = None,
) -> bool:
    """Кладёт материал. True — если он новый и его надо разбирать."""
    th = _title_hash(title)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        seen = _conn.execute(
            "SELECT 1 FROM items WHERE title_hash = ? LIMIT 1", (th,)
        ).fetchone()
        is_dup = 1 if seen else 0
        cur = _conn.execute(
            "INSERT OR IGNORE INTO items"
            "(source, ext_id, url, title, title_hash, text, category,"
            " published, first_seen, status, is_dup)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source,
                str(ext_id),
                url,
                title,
                th,
                text,
                category,
                published.astimezone(timezone.utc).isoformat(),
                now,
                "dup" if is_dup else "new",
                is_dup,
            ),
        )
        _conn.commit()
    return cur.rowcount > 0 and not is_dup


def known_ext_ids(source: str) -> set[str]:
    with _lock:
        rows = _conn.execute(
            "SELECT ext_id FROM items WHERE source = ?", (source,)
        ).fetchall()
    return {r["ext_id"] for r in rows}


def pending(limit: int = 60) -> list[sqlite3.Row]:
    with _lock:
        return _conn.execute(
            "SELECT * FROM items WHERE status='new' ORDER BY published LIMIT ?",
            (limit,),
        ).fetchall()


def save_extraction(item_id: int, data: dict) -> None:
    status = "parsed" if data.get("relevant") else "skipped"
    with _lock:
        _conn.execute(
            "UPDATE items SET status=?, buyer=?, seller=?, location=?, object=?,"
            " amount=?, area=?, stage=?, kind=?, done=?, summary=?,"
            " district=?, okrug=?, segment=?, obj_class=?, priority=?,"
            " is_moscow=? WHERE id=?",
            (
                status,
                data.get("buyer"),
                data.get("seller"),
                data.get("location"),
                data.get("object"),
                data.get("amount"),
                data.get("area"),
                data.get("stage"),
                data.get("kind"),
                1 if data.get("done") else 0,
                data.get("summary"),
                data.get("district"),
                data.get("okrug"),
                data.get("segment"),
                data.get("obj_class"),
                data.get("priority"),
                1 if data.get("is_moscow", True) else 0,
                item_id,
            ),
        )
        _conn.commit()


def mark_error(item_id: int) -> None:
    with _lock:
        _conn.execute("UPDATE items SET status='error' WHERE id=?", (item_id,))
        _conn.commit()


def _since(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def deals(days: float, location: str | None = None) -> list[sqlite3.Row]:
    """Состоявшиеся сделки — они и попадают в карточки. Только Москва."""
    sql = (
        "SELECT * FROM items WHERE status='parsed' AND is_dup=0 AND done=1"
        f" AND is_moscow=1 AND kind IN ({_placeholders(len(DEAL_KINDS))})"
        " AND published >= ?"
    )
    args: list = [*DEAL_KINDS, _since(days)]
    if location:
        sql += " AND location LIKE ?"
        args.append(f"%{location}%")
    sql += " ORDER BY published DESC"
    with _lock:
        return _conn.execute(sql, args).fetchall()


def in_progress(days: float, location: str | None = None) -> list[sqlite3.Row]:
    """Слухи, переговоры, выставленное на продажу. Только Москва."""
    sql = (
        "SELECT * FROM items WHERE status='parsed' AND is_dup=0 AND done=0"
        f" AND is_moscow=1 AND kind IN ({_placeholders(len(DEAL_KINDS))})"
        " AND published >= ?"
    )
    args: list = [*DEAL_KINDS, _since(days)]
    if location:
        sql += " AND location LIKE ?"
        args.append(f"%{location}%")
    sql += " ORDER BY published DESC"
    with _lock:
        return _conn.execute(sql, args).fetchall()


def rest(days: float) -> list[sqlite3.Row]:
    """Несделочное (аналитика, назначения) ИЛИ не про Москву. По запросу."""
    sql = (
        "SELECT * FROM items WHERE status='parsed' AND is_dup=0"
        f" AND (kind IS NULL OR kind NOT IN ({_placeholders(len(DEAL_KINDS))})"
        " OR is_moscow=0)"
        " AND published >= ? ORDER BY published DESC"
    )
    with _lock:
        return _conn.execute(sql, [*DEAL_KINDS, _since(days)]).fetchall()


def stats() -> dict:
    with _lock:
        by_status = _conn.execute(
            "SELECT status, COUNT(*) c FROM items GROUP BY status"
        ).fetchall()
        by_source = _conn.execute(
            "SELECT source, COUNT(*) c FROM items GROUP BY source"
        ).fetchall()
    return {
        "status": {r["status"]: r["c"] for r in by_status},
        "source": {r["source"]: r["c"] for r in by_source},
    }


def source_comparison(days: float = 30) -> dict:
    """Сколько уникальных сделок дал каждый источник — ради проверки полноты."""
    since = _since(days)
    with _lock:
        rows = _conn.execute(
            "SELECT source, is_dup, COUNT(*) c FROM items"
            " WHERE status='parsed' AND published >= ?"
            " GROUP BY source, is_dup",
            (since,),
        ).fetchall()
        overlap = _conn.execute(
            "SELECT COUNT(*) c FROM ("
            "  SELECT title_hash FROM items WHERE published >= ?"
            "  GROUP BY title_hash HAVING COUNT(DISTINCT source) > 1)",
            (since,),
        ).fetchone()
    result: dict = {}
    for r in rows:
        entry = result.setdefault(r["source"], {"unique": 0, "dup": 0})
        entry["dup" if r["is_dup"] else "unique"] += r["c"]
    result["_overlap"] = overlap["c"] if overlap else 0
    return result


# --- настройки, которые можно менять прямо из бота ---

def get_setting(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = _conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        _conn.commit()


def reset_parsed() -> int:
    """Помечает всё разобранное как новое, чтобы переразобрать другим режимом.

    Сами материалы не перезагружаются — только результат разбора.
    """
    with _lock:
        cur = _conn.execute(
            "UPDATE items SET status='new', buyer=NULL, seller=NULL, location=NULL,"
            " object=NULL, amount=NULL, area=NULL, stage=NULL, kind=NULL,"
            " done=0, summary=NULL"
            " WHERE status IN ('parsed','skipped','error')"
        )
        _conn.commit()
    return cur.rowcount


# --- объекты (площадки): накопление истории ------------------------------

def all_objects() -> list[sqlite3.Row]:
    with _lock:
        return _conn.execute("SELECT * FROM objects").fetchall()


def get_object(object_id: int) -> sqlite3.Row | None:
    with _lock:
        return _conn.execute(
            "SELECT * FROM objects WHERE id = ?", (object_id,)
        ).fetchone()


def create_object(name: str, stems: list[str], published: str,
                  fields: dict, core: list[str] | None = None) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO objects(name, stems, core, first_seen, last_seen,"
            " events, buyer, seller, location, district, okrug, segment,"
            " obj_class, amount, area, stage)"
            " VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)",
            (name, json.dumps(sorted(stems), ensure_ascii=False),
             json.dumps(sorted(core or stems), ensure_ascii=False),
             published, published,
             fields.get("buyer"), fields.get("seller"), fields.get("location"),
             fields.get("district"), fields.get("okrug"), fields.get("segment"),
             fields.get("obj_class"), fields.get("amount"), fields.get("area"),
             fields.get("stage")),
        )
        _conn.commit()
    return cur.lastrowid


def update_object(object_id: int, stems: list[str], published: str,
                  fields: dict) -> None:
    """Дополняет объект: пустые поля заполняются, известные не затираются."""
    with _lock:
        row = _conn.execute(
            "SELECT * FROM objects WHERE id = ?", (object_id,)
        ).fetchone()
        if row is None:
            return

        merged = sorted(set(json.loads(row["stems"])) | set(stems))
        # Свежие сведения важнее: stage перезаписываем всегда, остальное —
        # только если раньше было пусто.
        vals = {}
        for f in ("buyer", "seller", "location", "district", "okrug",
                  "segment", "obj_class", "amount", "area"):
            vals[f] = row[f] or fields.get(f)
        vals["stage"] = fields.get("stage") or row["stage"]

        _conn.execute(
            "UPDATE objects SET stems=?, last_seen=?, events=events+1,"
            " buyer=?, seller=?, location=?, district=?, okrug=?, segment=?,"
            " obj_class=?, amount=?, area=?, stage=? WHERE id=?",
            (json.dumps(merged, ensure_ascii=False),
             max(row["last_seen"], published),
             vals["buyer"], vals["seller"], vals["location"], vals["district"],
             vals["okrug"], vals["segment"], vals["obj_class"], vals["amount"],
             vals["area"], vals["stage"], object_id),
        )
        _conn.commit()


def link_item(item_id: int, object_id: int) -> None:
    with _lock:
        _conn.execute("UPDATE items SET object_id=? WHERE id=?",
                      (object_id, item_id))
        _conn.commit()


def object_timeline(object_id: int) -> list[sqlite3.Row]:
    """Все материалы об объекте, от старых к новым."""
    with _lock:
        return _conn.execute(
            "SELECT * FROM items WHERE object_id = ? AND is_dup = 0"
            " ORDER BY published",
            (object_id,),
        ).fetchall()


def unlinked_parsed(limit: int = 500) -> list[sqlite3.Row]:
    """Разобранные материалы, ещё не привязанные к объекту."""
    with _lock:
        return _conn.execute(
            "SELECT * FROM items WHERE status='parsed' AND object_id IS NULL"
            " ORDER BY published LIMIT ?", (limit,)
        ).fetchall()


def top_objects(days: float = 90, limit: int = 20) -> list[sqlite3.Row]:
    """Объекты с наибольшим числом новостей за период."""
    with _lock:
        return _conn.execute(
            "SELECT * FROM objects WHERE last_seen >= ?"
            " ORDER BY events DESC, last_seen DESC LIMIT ?",
            (_since(days), limit),
        ).fetchall()


def reset_objects() -> None:
    """Сброс накопленной истории — для пересборки после смены правил."""
    with _lock:
        _conn.execute("DELETE FROM objects")
        _conn.execute("UPDATE items SET object_id = NULL")
        _conn.commit()
