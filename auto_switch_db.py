"""provider-auto-switch — SQLite data layer.

Shared between the hooks plugin (__init__.py) and the dashboard API
(plugin_api.py). Uses WAL mode so concurrent reads/writes work.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "provider-auto-switch.db"

# Thread-local connections so each plugin/hook thread gets its own
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection (WAL mode)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS switch_config (
    profile_name    TEXT PRIMARY KEY,
    strategy        TEXT NOT NULL DEFAULT 'model_first',
    auto_switch     INTEGER NOT NULL DEFAULT 1,
    manual_override INTEGER NOT NULL DEFAULT 0,
    model_priority  TEXT NOT NULL DEFAULT '[]',
    provider_priority TEXT NOT NULL DEFAULT '[]',
    scan_interval   INTEGER NOT NULL DEFAULT 30,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scan_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name    TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    provider_name   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'unknown',
    last_available_at TEXT,
    next_check_at   TEXT,
    error_reason    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(profile_name, model_name, provider_name)
);

CREATE TABLE IF NOT EXISTS switch_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name    TEXT NOT NULL,
    from_model      TEXT NOT NULL,
    from_provider   TEXT NOT NULL,
    to_model        TEXT NOT NULL,
    to_provider     TEXT NOT NULL,
    reason          TEXT NOT NULL,
    triggered_by    TEXT NOT NULL DEFAULT 'auto',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS active_combo (
    profile_name    TEXT PRIMARY KEY,
    model_name      TEXT NOT NULL,
    provider_name   TEXT NOT NULL,
    config_updated  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class SwitchConfig:
    profile_name: str
    strategy: str = "model_first"
    auto_switch: bool = True
    manual_override: bool = False
    model_priority: list[str] = field(default_factory=list)
    provider_priority: list[str] = field(default_factory=list)
    scan_interval: int = 30

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SwitchConfig":
        return cls(
            profile_name=row["profile_name"],
            strategy=row["strategy"],
            auto_switch=bool(row["auto_switch"]),
            manual_override=bool(row["manual_override"]),
            model_priority=json.loads(row["model_priority"]),
            provider_priority=json.loads(row["provider_priority"]),
            scan_interval=row["scan_interval"],
        )


@dataclass
class ScanSnapshot:
    profile_name: str
    model_name: str
    provider_name: str
    status: str = "unknown"  # active | limited | rate_limited | unavailable
    last_available_at: Optional[str] = None
    next_check_at: Optional[str] = None
    error_reason: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ScanSnapshot":
        return cls(
            profile_name=row["profile_name"],
            model_name=row["model_name"],
            provider_name=row["provider_name"],
            status=row["status"],
            last_available_at=row["last_available_at"],
            next_check_at=row["next_check_at"],
            error_reason=row["error_reason"],
        )


@dataclass
class SwitchHistory:
    profile_name: str
    from_model: str
    from_provider: str
    to_model: str
    to_provider: str
    reason: str
    triggered_by: str = "auto"
    created_at: str = ""


# ---------------------------------------------------------------------------
# CRUD — SwitchConfig
# ---------------------------------------------------------------------------

def get_config(profile_name: str) -> Optional[SwitchConfig]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM switch_config WHERE profile_name = ?", (profile_name,)
    ).fetchone()
    return SwitchConfig.from_row(row) if row else None


def upsert_config(cfg: SwitchConfig) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO switch_config (profile_name, strategy, auto_switch, manual_override,
           model_priority, provider_priority, scan_interval, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(profile_name) DO UPDATE SET
               strategy=excluded.strategy,
               auto_switch=excluded.auto_switch,
               manual_override=excluded.manual_override,
               model_priority=excluded.model_priority,
               provider_priority=excluded.provider_priority,
               scan_interval=excluded.scan_interval,
               updated_at=datetime('now')""",
        (cfg.profile_name, cfg.strategy, int(cfg.auto_switch),
         int(cfg.manual_override),
         json.dumps(cfg.model_priority), json.dumps(cfg.provider_priority),
         cfg.scan_interval),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# CRUD — ScanSnapshot
# ---------------------------------------------------------------------------

def upsert_snapshot(snap: ScanSnapshot) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO scan_snapshot (profile_name, model_name, provider_name, status,
           last_available_at, next_check_at, error_reason, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(profile_name, model_name, provider_name) DO UPDATE SET
               status=excluded.status,
               last_available_at=excluded.last_available_at,
               next_check_at=excluded.next_check_at,
               error_reason=excluded.error_reason,
               updated_at=datetime('now')""",
        (snap.profile_name, snap.model_name, snap.provider_name,
         snap.status, snap.last_available_at, snap.next_check_at,
         snap.error_reason),
    )
    conn.commit()


def get_snapshots(profile_name: str) -> list[ScanSnapshot]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM scan_snapshot WHERE profile_name = ? ORDER BY model_name, provider_name",
        (profile_name,),
    ).fetchall()
    return [ScanSnapshot.from_row(r) for r in rows]


def clear_snapshots(profile_name: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM scan_snapshot WHERE profile_name = ?", (profile_name,))
    conn.commit()


# ---------------------------------------------------------------------------
# CRUD — ActiveCombo
# ---------------------------------------------------------------------------

def get_active_combo(profile_name: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM active_combo WHERE profile_name = ?", (profile_name,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def set_active_combo(profile_name: str, model: str, provider: str) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO active_combo (profile_name, model_name, provider_name, config_updated, updated_at)
           VALUES (?, ?, ?, 1, datetime('now'))
           ON CONFLICT(profile_name) DO UPDATE SET
               model_name=excluded.model_name,
               provider_name=excluded.provider_name,
               config_updated=1,
               updated_at=datetime('now')""",
        (profile_name, model, provider),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# CRUD — SwitchHistory
# ---------------------------------------------------------------------------

def add_history(h: SwitchHistory) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO switch_history (profile_name, from_model, from_provider,
           to_model, to_provider, reason, triggered_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (h.profile_name, h.from_model, h.from_provider,
         h.to_model, h.to_provider, h.reason, h.triggered_by),
    )
    conn.commit()


def get_history(profile_name: str, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM switch_history WHERE profile_name = ? ORDER BY created_at DESC LIMIT ?",
        (profile_name, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def list_profiles_from_config() -> list[str]:
    """Discover profile names from ~/.hermes/profiles/ directory + root config.yaml."""
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    profiles = []
    
    # Always include "default" if root config.yaml exists
    default_cfg = hermes_home / "config.yaml"
    if default_cfg.exists():
        profiles.append("default")
    
    # Scan profiles/ subdirectory
    profiles_dir = hermes_home / "profiles"
    if profiles_dir.is_dir():
        profiles.extend(
            d.name for d in sorted(profiles_dir.iterdir())
            if d.is_dir() and (d / "config.yaml").exists()
        )
    
    return profiles


def get_all_configs() -> list[dict]:
    """Return all SwitchConfig + active_combo as a list of dicts."""
    profiles = list_profiles_from_config()
    results = []
    for pname in profiles:
        cfg = get_config(pname)
        combo = get_active_combo(pname)
        results.append({
            "profile_name": pname,
            "config": asdict(cfg) if cfg else None,
            "active_combo": combo,
        })
    return results
