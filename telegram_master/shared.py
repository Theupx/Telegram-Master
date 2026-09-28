# -*- coding: utf-8 -*-

import os
import json
import asyncio
import time
import shutil
import logging
import logging.handlers
import re
import sqlite3
import tempfile
import io
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient, events, Button
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    AuthKeyError,
    RPCError,
    PasswordHashInvalidError,
)
