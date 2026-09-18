"""Сжатие истории диалога — главный источник экономии и единственное место,
где качество может пострадать.

Почему это вообще нужно: API без состояния, вся история пересылается каждый
ход. Для треда из N ходов по L токенов суммарный расход ~ L*N^2/2 — тред на
60 ходов стоит примерно в 30 раз дороже первого хода при том же качестве.
Сжатие возвращает рост к линейному.

Три уровня, чтобы ничего не потерять:
  1. последние N ходов — дословно;
  2. пины (цифры, даты, условия, решения) — дословно, навсегда;
  3. всё остальное — одна сводка, пересчитываемая инкрементально.
"""

from __future__ import annotations

import time

from . import guard

WINDOWS = {"economy": 4, "normal": 12, "full": 10_000}

SUMMARY_PROMPT = """Ниже предыдущая сводка диалога и новые сообщения, которые нужно в неё добавить.
Верни обновлённую сводку одним текстом, не длиннее 250 слов.

Обязательно сохрани: кто что решил, какие варианты отвергнуты и почему, какие
файлы обсуждались, какие задачи остались открытыми.
Не пересказывай цифры, даты, суммы и формулировки условий — они сохраняются
отдельно, дословно. Не добавляй ничего от себя.

ПРЕДЫДУЩАЯ СВОДКА:
{previous}

НОВЫЕ СООБЩЕНИЯ:
{new}"""


def ensure_session(conn, session: str) -> None:
    conn.execute("INSERT OR IGNORE INTO sessions (id, created_at) VALUES (?,?)",
                 (session, time.time()))
    conn.commit()


def append_turn(conn, session: str, role: str, content: str) -> int:
    ensure_session(conn, session)
    row = conn.execute("SELECT COALESCE(MAX(idx), -1) m FROM turns WHERE session = ?",
                       (session,)).fetchone()
    idx = row["m"] + 1
    conn.execute("INSERT INTO turns (session, idx, role, content, ts) VALUES (?,?,?,?,?)",
                 (session, idx, role, content, time.time()))
    # Пины снимаются сразу при записи хода, а не при вытеснении: так они не
    # зависят от того, дожил ли ход до сжатия.
    for kind, text, source in guard.extract_pins(content, f"ход {idx} ({role})"):
        conn.execute("INSERT INTO pins (session, kind, text, source, ts) VALUES (?,?,?,?,?)",
                     (session, kind, text, source, time.time()))
    conn.commit()
    return idx


def all_turns(conn, session: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM turns WHERE session = ? ORDER BY idx",
                        (session,)).fetchall()
    return [{"idx": r["idx"], "role": r["role"], "content": r["content"]} for r in rows]


def pins_block(conn, session: str, limit: int = 120) -> str:
    rows = conn.execute(
        "SELECT kind, text, source FROM pins WHERE session = ? ORDER BY id DESC LIMIT ?",
        (session, limit)).fetchall()
    if not rows:
        return ""
    lines = [f"- [{r['kind']}] {r['text']}  ({r['source']})" for r in reversed(rows)]
    return ("Закреплённые данные из ранних сообщений — дословно, не пересказ:\n"
            + "\n".join(lines))


def pack(conn, session: str, mode: str, summarizer=None) -> tuple[str, str, list[dict]]:
    """Возвращает (сводка, пины, последние ходы для messages).

    summarizer — callable(prompt) -> str. Если не передан, вытесненные ходы
    не сворачиваются, а остаются в контексте: терять их молча нельзя.
    """
    ensure_session(conn, session)
    turns = all_turns(conn, session)
    window = WINDOWS.get(mode, WINDOWS["normal"])

    if len(turns) <= window:
        return "", pins_block(conn, session), turns

    row = conn.execute("SELECT summary, summarized_upto FROM sessions WHERE id = ?",
                       (session,)).fetchone()
    summary, upto = row["summary"], row["summarized_upto"]
    recent = turns[-window:]
    evicted = [t for t in turns[:-window] if t["idx"] >= upto]

    if evicted and summarizer is not None:
        new_text = "\n\n".join(f"[{t['role']}] {t['content'][:2000]}" for t in evicted)
        try:
            summary = summarizer(SUMMARY_PROMPT.format(
                previous=summary or "(сводки ещё нет)", new=new_text)).strip()
            upto = evicted[-1]["idx"] + 1
            conn.execute("UPDATE sessions SET summary = ?, summarized_upto = ? WHERE id = ?",
                         (summary, upto, session))
            conn.commit()
        except Exception:
            # Сводка не получилась — отдаём ходы как есть. Дороже, но без потерь.
            return "", pins_block(conn, session), turns
    elif evicted:
        return "", pins_block(conn, session), turns

    summary_block = f"Сводка более ранней части диалога:\n{summary}" if summary else ""
    return summary_block, pins_block(conn, session), recent


def to_messages(turns: list[dict]) -> list[dict]:
    return [{"role": t["role"], "content": t["content"]} for t in turns]
