"""Запуск бота на сервере.

Бот делает два дела одновременно: отвечает на команды и в фоне сам
ходит за новостями раз в час. Команда /update при этом никуда не
делась — она просто заставляет сделать обход прямо сейчас.
"""

import asyncio
import logging
from datetime import datetime

import bot as bot_module
import config
import db
import extractor
import sources
from sources import tg_channel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("main")


async def collect_cycle() -> tuple[int, int]:
    """Один обход источников. Возвращает (собрано, распознано)."""
    added = await asyncio.to_thread(sources.collect_all)
    parsed, _ = await extractor.process_pending(limit=200)
    return added, parsed


async def worker():
    """Фоновый сбор и утренняя сводка."""
    if not config.POLL_INTERVAL_MIN:
        log.info("Фоновый сбор выключен")
        return

    last_digest_date = None
    while True:
        try:
            added, parsed = await collect_cycle()
            log.info("Фоновый обход: собрано %s, распознано %s", added, parsed)

            # если окно превью могло переполниться — предупреждаем владельца
            if tg_channel.overflow:
                text = (
                    "⚠️ Возможен пропуск постов в каналах: "
                    + ", ".join(tg_channel.overflow)
                    + "\nМежду заходами вышло больше постов, чем помещается "
                    "на одной странице превью. Стоит уменьшить "
                    "POLL_INTERVAL_MIN."
                )
                for uid in config.OWNER_IDS:
                    try:
                        await bot_module.bot.send_message(uid, text)
                    except Exception:
                        log.exception("Не отправилось предупреждение")
        except Exception:
            log.exception("Сбой фонового обхода")

        if config.DAILY_DIGEST_HOUR is not None:
            now = datetime.now()
            if now.hour == config.DAILY_DIGEST_HOUR and last_digest_date != now.date():
                last_digest_date = now.date()
                for uid in config.OWNER_IDS:
                    try:
                        await bot_module.send_digest(uid, 1.0, "сутки")
                    except Exception:
                        log.exception("Не отправился автодайджест")

        await asyncio.sleep(config.POLL_INTERVAL_MIN * 60)


async def main():
    config.validate()
    db.init()
    log.info(
        "Источники: %s | сбор каждые %s мин",
        ", ".join(config.SOURCES),
        config.POLL_INTERVAL_MIN or "—",
    )
    await asyncio.gather(
        worker(),
        bot_module.dp.start_polling(bot_module.bot),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
