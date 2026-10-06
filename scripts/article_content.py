"""Fetch and truncate article body for news items (parallel + Google News batchexecute unwrap)."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

CONTENT_MAX_CHARS = 600
CONTENT_FETCH_TIMEOUT = 12
MAX_WORKERS = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}
OLD_PLACEHOLDER