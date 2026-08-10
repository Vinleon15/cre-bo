"""Запуск бота. Сбор происходит по команде /update, фонового цикла нет."""

import asyncio
import logging

import bot as bot_module
import config
import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


async def main():
    config.validate()
    db.init()
    logging.info("Источники: %s", ", ".join(config.SOURCES))
    await bot_module.dp.start_polling(bot_module.bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
