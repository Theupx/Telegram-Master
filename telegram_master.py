#!/usr/bin/env python3
"""Telegram Master launcher.

The implementation lives in the telegram_master package.
"""

import asyncio

from telegram_master.main import run


if __name__ == "__main__":
    asyncio.run(run())
