"""Отчёт об экономии: python -m tokensave.report [дней]"""

from __future__ import annotations

import sys
import time

from . import ledger, store


def main() -> int:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    conn = store.connect()
    s = ledger.summary(conn, since=time.time() - days * 86400)

    if not s["requests"]:
        print(f"За последние {days:g} дн. запросов не было.")
        return 0

    print(f"За последние {days:g} дн. — {s['requests']} запросов\n")
    print(f"  входные токены      {s['input_tokens']:>12,}")
    print(f"  чтение из кэша      {s['cache_read']:>12,}  (0.1x цены)")
    print(f"  запись в кэш        {s['cache_write']:>12,}  (1.25x цены)")
    print(f"  выходные токены     {s['output_tokens']:>12,}")
    print(f"\n  без оптимизации     ${s['baseline_usd']:>11.2f}")
    print(f"  фактически          ${s['cost_usd']:>11.2f}")
    print(f"  сэкономлено         ${s['saved_usd']:>11.2f}   ({s['saved_pct']:.1f}%)")
    print(f"\n  попаданий в кэш     {s['cache_hit_rate']:.1f}% входных токенов")
    print(f"  эскалаций контекста {s['escalations']} "
          f"({s['escalations'] / s['requests'] * 100:.0f}% запросов)")

    if s["cache_hit_rate"] < 10 and s["requests"] > 3:
        print("\n  Низкий процент кэша. Скорее всего префикс промпта меняется "
              "между запросами — проверьте предупреждения в Answer.warnings.")
    if s["escalations"] / s["requests"] > 0.3:
        print("\n  Много эскалаций: режим слишком экономный для ваших задач. "
              "Переключитесь на normal/full — так дешевле, чем платить дважды.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
