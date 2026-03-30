"""
Persist Xero refresh token (and optional tenant id) on disk so rotation survives restarts.

Uses SQLite (stdlib). Set XERO_TOKEN_SQLITE_PATH (default: /tmp/hubspot_xero_tokens.db).
On Railway, mount a volume at /data and set XERO_TOKEN_SQLITE_PATH=/data/xero_tokens.db
so tokens survive redeploys.

Disable with XERO_DISABLE_TOKEN_STORE=1 (env-only mode).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

_TABLE = """CREATE TABLE IF NOT EXISTS xero_oauth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    refresh_token TEXT,
    tenant_id TEXT
)"""


def _enabled() -> bool:
    return (os.getenv("XERO_DISABLE_TOKEN_STORE") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def is_token_store_enabled() -> bool:
    """True when rotated refresh tokens are persisted to disk (see XERO_TOKEN_SQLITE_PATH)."""
    return _enabled()


def get_resolved_sqlite_path() -> str:
    """Absolute path used for the token database (for /api/status diagnostics)."""
    return _path()


def _path() -> str:
    p = (os.getenv("XERO_TOKEN_SQLITE_PATH") or "").strip()
    if p:
        return p
    return "/tmp/hubspot_xero_tokens.db"


def _ensure_db() -> None:
    if not _enabled():
        return
    path = _path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(_TABLE)
        conn.commit()


def get_stored_refresh_token() -> Optional[str]:
    if not _enabled():
        return None
    try:
        _ensure_db()
        with sqlite3.connect(_path()) as conn:
            row = conn.execute(
                "SELECT refresh_token FROM xero_oauth WHERE id = 1"
            ).fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    except Exception:
        return None
    return None


def get_stored_tenant_id() -> Optional[str]:
    if not _enabled():
        return None
    try:
        _ensure_db()
        with sqlite3.connect(_path()) as conn:
            row = conn.execute("SELECT tenant_id FROM xero_oauth WHERE id = 1").fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    except Exception:
        return None
    return None


def save_refresh_token(token: str) -> None:
    if not _enabled():
        return
    t = (token or "").strip()
    if not t:
        return
    _ensure_db()
    with sqlite3.connect(_path()) as conn:
        conn.execute(
            """INSERT INTO xero_oauth (id, refresh_token) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET refresh_token = excluded.refresh_token""",
            (t,),
        )
        conn.commit()
    try:
        os.chmod(_path(), 0o600)
    except OSError:
        pass


def save_tenant_id(tenant_id: str) -> None:
    if not _enabled():
        return
    t = (tenant_id or "").strip()
    if not t:
        return
    _ensure_db()
    with sqlite3.connect(_path()) as conn:
        conn.execute(
            """INSERT INTO xero_oauth (id, tenant_id) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET tenant_id = excluded.tenant_id""",
            (t,),
        )
        conn.commit()
    try:
        os.chmod(_path(), 0o600)
    except OSError:
        pass


def save_after_oauth(*, refresh_token: str, tenant_id: Optional[str]) -> None:
    """Called after browser OAuth — persist refresh; tenant only if provided."""
    save_refresh_token(refresh_token)
    if tenant_id:
        save_tenant_id(tenant_id)
