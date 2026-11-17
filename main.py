"""Запуск бота: python main.py (то же самое, что python -m bot).

Пробный сбор новостей без Telegram: python main.py --preview
"""

import sys

from bot.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
