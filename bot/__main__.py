"""Entry point: ``python -m bot`` (or ``python -m bot --preview``)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import ConfigError, load_config
from .logging_setup import setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot", description="Футбольный новостной бот")
    parser.add_argument("--preview", action="store_true",
                        help="один раз собрать новости и вывести в консоль, без Telegram")
    args = parser.parse_args(argv)

    try:
        config = load_config(require_token=not args.preview)
    except ConfigError as exc:
        print(f"Ошибка настройки: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_level, config.log_dir)
    try:
        if args.preview:
            from .preview import preview
            return asyncio.run(preview(config))
        from .app import run
        return asyncio.run(run(config))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Остановлено пользователем")
        return 0


if __name__ == "__main__":
    sys.exit(main())
