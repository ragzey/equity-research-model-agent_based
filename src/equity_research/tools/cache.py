"""Local SQLite cache with TTL for Yahoo, SEC, and other sourced payloads."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("LocalCache")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
ISO_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}")

TTL_STATEMENTS = int(os.getenv("CACHE_TTL_STATEMENTS", str(12 * 3600)))
TTL_SEC = int(os.getenv("CACHE_TTL_SEC", str(7 * 24 * 3600)))
TTL_TICKER_MAP = int(os.getenv("CACHE_TTL_TICKER_MAP", str(24 * 3600)))
TTL_PEERS = int(os.getenv("CACHE_TTL_PEERS", str(12 * 3600)))
TTL_BONDS = int(os.getenv("CACHE_TTL_BONDS", str(12 * 3600)))
TTL_CONSENSUS = int(os.getenv("CACHE_TTL_CONSENSUS", str(12 * 3600)))
TTL_TREASURY = int(os.getenv("CACHE_TTL_TREASURY", str(6 * 3600)))
TTL_DAMODARAN = int(os.getenv("CACHE_TTL_DAMODARAN", str(30 * 24 * 3600)))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cache_db_path() -> Path:
    override = os.getenv("CACHE_DB_PATH", "").strip()
    if override:
        return Path(override)
    return CACHE_DIR / "pipeline_cache.sqlite"


def _connect() -> sqlite3.Connection:
    path = cache_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            stored_at TEXT NOT NULL
        )
        """
    )
    return connection


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if type(value).__module__ == "numpy":
        try:
            return to_jsonable(value.item())
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return str(value)


def _parse_iso_key(key: str) -> Any:
    normalized = key.replace("Z", "").replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(key[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return key


def _revive(value: Any) -> Any:
    if isinstance(value, dict):
        revived: dict[Any, Any] = {}
        for key, item in value.items():
            new_key: Any = key
            if isinstance(key, str) and ISO_KEY.match(key):
                new_key = _parse_iso_key(key)
            revived[new_key] = _revive(item)
        return revived
    if isinstance(value, list):
        return [_revive(item) for item in value]
    return value


def cache_get(namespace: str, key: str, ttl_seconds: int) -> Optional[Any]:
    cache_key = f"{namespace}:{key}"
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT payload, stored_at FROM cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
    except sqlite3.Error:
        logger.exception("Cache read failed for %s", cache_key)
        return None
    if not row:
        return None
    payload, stored_at = row
    try:
        stored = datetime.fromisoformat(stored_at)
    except ValueError:
        return None
    age = (_utc_now() - stored).total_seconds()
    if age > ttl_seconds:
        return None
    try:
        return _revive(json.loads(payload))
    except json.JSONDecodeError:
        return None


def cache_set(namespace: str, key: str, value: Any) -> None:
    cache_key = f"{namespace}:{key}"
    payload = json.dumps(to_jsonable(value), ensure_ascii=False)
    try:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO cache (cache_key, payload, stored_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    stored_at = excluded.stored_at
                """,
                (cache_key, payload, _utc_now().isoformat(timespec="seconds")),
            )
            connection.commit()
    except sqlite3.Error:
        logger.exception("Cache write failed for %s", cache_key)
