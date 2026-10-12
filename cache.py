"""A small disk cache for web lookups (Wikipedia, Grokipedia, SearXNG, web pages).

Why: the search tests ask the same questions every run, and the model's lookup steps
run at temperature 0, so each run requests exactly the same URLs. Without a cache,
~60 Wikipedia requests per test run got rate-limited (HTTP 429) and late questions
failed for reasons that had nothing to do with the strategy being tested. With it,
every version is compared on the same source texts and the sites are asked only once.

Entries expire after MAX_AGE_DAYS. Only successful results are stored, so a failed
lookup is simply tried again next time. Live data (weather) must not use this.
Delete the cache/ folder to start fresh.
"""

import hashlib
import json
import time
from functools import wraps
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
MAX_AGE_DAYS = 1


def cached(function):
    """Remember function(*args) on disk. The result must be JSON-serialisable."""
    @wraps(function)
    def wrapper(*args):
        # Keyed by function name + arguments only (not the module), so a frozen copy
        # of sources.py and the working copy share the same cached texts.
        key = json.dumps([function.__name__, args], ensure_ascii=False)
        path = CACHE_DIR / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")
        if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_DAYS * 86400:
            return json.loads(path.read_text(encoding="utf-8"))["result"]
        result = function(*args)
        if result:  # don't remember failures or empty results
            CACHE_DIR.mkdir(exist_ok=True)
            path.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False),
                            encoding="utf-8")
        return result
    return wrapper
