"""Журнал расхода токенов и расчёт экономии.

Каждый запрос пишется дважды: фактическая стоимость и baseline — сколько
стоил бы тот же запрос без оптимизации (вся история, все файлы целиком,
без кэша). Разница и есть экономия. Без baseline любые проценты — выдумка.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# $ за 1M токенов. Источник — прайс Anthropic; перепроверяйте при обновлении моделей.
# cache_read = 0.1x input, cache_write(5m) = 1.25x input, cache_write(1h) = 2x input.
PRICES = {
    "claude-opus-5":    {"in": 5.00,  "out": 25.00},
    "claude-opus-4-8":  {"in": 5.00,  "out": 25.00},
    "claude-sonnet-5":  {"in": 2.00,  "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00,  "out": 5.00},
    "claude-fable-5-1": {"in": 10.00, "out": 50.00, "cache_read": 0.25},
}
_FALLBACK = {"in": 5.00, "out": 25.00}


def _price(model: str) -> dict:
    return PRICES.get(model, _FALLBACK)


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0, ttl_1h: bool = False) -> float:
    p = _price(model)
    read_rate = p.get("cache_read", p["in"] * 0.1)
    write_rate = p["in"] * (2.0 if ttl_1h else 1.25)
    return (
        input_tokens * p["in"]
        + output_tokens * p["out"]
        + cache_read * read_rate
        + cache_write * write_rate
    ) / 1_000_000


@dataclass
class Entry:
    session: str
    mode: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    baseline_input: int = 0
    escalated: bool = False
    note: str = ""
    ttl_1h: bool = False

    @property
    def actual_usd(self) -> float:
        return cost_usd(self.model, self.input_tokens, self.output_tokens,
                        self.cache_read, self.cache_write, self.ttl_1h)

    @property
    def baseline_usd(self) -> float:
        """Без кэша и без сжатия: весь контекст оплачен по полной ставке input."""
        return cost_usd(self.model, self.baseline_input, self.output_tokens)


def record(conn, entry: Entry) -> None:
    conn.execute(
        """INSERT INTO ledger (ts, session, mode, model, input_tokens, output_tokens,
                               cache_read, cache_write, baseline_input, cost_usd,
                               baseline_usd, escalated, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (time.time(), entry.session, entry.mode, entry.model, entry.input_tokens,
         entry.output_tokens, entry.cache_read, entry.cache_write, entry.baseline_input,
         entry.actual_usd, entry.baseline_usd, int(entry.escalated), entry.note),
    )
    conn.commit()


def summary(conn, since: float | None = None) -> dict:
    where, args = ("WHERE ts >= ?", (since,)) if since else ("", ())
    row = conn.execute(f"""
        SELECT COUNT(*) n,
               SUM(input_tokens) inp, SUM(output_tokens) outp,
               SUM(cache_read) cr, SUM(cache_write) cw,
               SUM(baseline_input) base, SUM(cost_usd) cost,
               SUM(baseline_usd) base_cost, SUM(escalated) esc
        FROM ledger {where}""", args).fetchone()

    n = row["n"] or 0
    cost, base_cost = row["cost"] or 0.0, row["base_cost"] or 0.0
    total_in = (row["inp"] or 0) + (row["cr"] or 0) + (row["cw"] or 0)
    return {
        "requests": n,
        "input_tokens": row["inp"] or 0,
        "output_tokens": row["outp"] or 0,
        "cache_read": row["cr"] or 0,
        "cache_write": row["cw"] or 0,
        "baseline_input": row["base"] or 0,
        "cost_usd": cost,
        "baseline_usd": base_cost,
        "saved_usd": base_cost - cost,
        "saved_pct": (1 - cost / base_cost) * 100 if base_cost else 0.0,
        "cache_hit_rate": (row["cr"] or 0) / total_in * 100 if total_in else 0.0,
        "escalations": row["esc"] or 0,
    }
