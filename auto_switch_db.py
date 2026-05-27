"""provider-auto-switch: Database layer (SQLite + migration)"""

import json
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_DIR = Path.home() / ".hermes"
DB_PATH = DB_DIR / "provider-auto-switch.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS switch_config (
    profile_name TEXT PRIMARY KEY,
    strategy TEXT NOT NULL DEFAULT 'model_first',
    auto_switch INTEGER NOT NULL DEFAULT 1,
    manual_override INTEGER NOT NULL DEFAULT 0,
    model_priority TEXT NOT NULL DEFAULT '[]',
    provider_priority TEXT NOT NULL DEFAULT '[]',
    model_providers TEXT NOT NULL DEFAULT '{}',
    provider_models TEXT NOT NULL DEFAULT '{}',
    scan_interval INTEGER NOT NULL DEFAULT 30,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scan_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_available_at TEXT,
    next_check_at TEXT,
    error_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(profile_name, model_name, provider_name)
);

CREATE TABLE IF NOT EXISTS switch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT NOT NULL,
    from_model TEXT NOT NULL,
    from_provider TEXT NOT NULL,
    to_model TEXT NOT NULL,
    to_provider TEXT NOT NULL,
    reason TEXT NOT NULL,
    triggered_by TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS active_combo (
    profile_name TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    config_updated INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Thread-local connection pool
# ---------------------------------------------------------------------------

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    """Create tables and migrate schema if needed."""
    conn = _get_conn()
    conn.executescript(SCHEMA_SQL)

    # Migrate: add columns that may not exist in older DBs
    for col in ("model_providers", "provider_models"):
        try:
            conn.execute(f"ALTER TABLE switch_config ADD COLUMN {col} TEXT NOT NULL DEFAULT '{{}}'")
        except sqlite3.OperationalError:
            pass  # already exists

    conn.commit()


def close_db() -> None:
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SwitchConfig:
    profile_name: str
    strategy: str = "model_first"
    auto_switch: bool = True
    manual_override: bool = False
    model_priority: list[str] = field(default_factory=list)
    provider_priority: list[str] = field(default_factory=list)
    model_providers: dict[str, list[str]] = field(default_factory=dict)
    provider_models: dict[str, list[str]] = field(default_factory=dict)
    scan_interval: int = 30


@dataclass
class ScanEntry:
    id: int = 0
    profile_name: str = ""
    model_name: str = ""
    provider_name: str = ""
    status: str = "unknown"
    last_available_at: Optional[str] = None
    next_check_at: Optional[str] = None
    error_reason: Optional[str] = None


@dataclass
class SwitchHistoryEntry:
    id: int = 0
    profile_name: str = ""
    from_model: str = ""
    from_provider: str = ""
    to_model: str = ""
    to_provider: str = ""
    reason: str = ""
    triggered_by: str = "auto"
    created_at: str = ""


@dataclass
class ActiveCombo:
    profile_name: str = ""
    model_name: str = ""
    provider_name: str = ""
    config_updated: bool = False


# ---------------------------------------------------------------------------
# CRUD: SwitchConfig
# ---------------------------------------------------------------------------


def _row_to_config(row: sqlite3.Row) -> SwitchConfig:
    return SwitchConfig(
        profile_name=row["profile_name"],
        strategy=row["strategy"],
        auto_switch=bool(row["auto_switch"]),
        manual_override=bool(row["manual_override"]),
        model_priority=json.loads(row["model_priority"] or "[]"),
        provider_priority=json.loads(row["provider_priority"] or "[]"),
        model_providers=json.loads(row["model_providers"] or "{}"),
        provider_models=json.loads(row["provider_models"] or "{}"),
        scan_interval=row["scan_interval"],
    )


def get_config(profile: str) -> Optional[SwitchConfig]:
    init_db()
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM switch_config WHERE profile_name = ?", (profile,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_config(row)


def upsert_config(cfg: SwitchConfig) -> SwitchConfig:
    init_db()
    conn = _get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """INSERT INTO switch_config
           (profile_name, strategy, auto_switch, manual_override,
            model_priority, provider_priority,
            model_providers, provider_models,
            scan_interval, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(profile_name) DO UPDATE SET
               strategy=excluded.strategy,
               auto_switch=excluded.auto_switch,
               manual_override=excluded.manual_override,
               model_priority=excluded.model_priority,
               provider_priority=excluded.provider_priority,
               model_providers=excluded.model_providers,
               provider_models=excluded.provider_models,
               scan_interval=excluded.scan_interval,
               updated_at=excluded.updated_at""",
        (
            cfg.profile_name,
            cfg.strategy,
            int(cfg.auto_switch),
            int(cfg.manual_override),
            json.dumps(cfg.model_priority),
            json.dumps(cfg.provider_priority),
            json.dumps(cfg.model_providers),
            json.dumps(cfg.provider_models),
            cfg.scan_interval,
            now,
        ),
    )
    conn.commit()
    return cfg


def list_configs() -> list[SwitchConfig]:
    init_db()
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM switch_config ORDER BY profile_name").fetchall()
    return [_row_to_config(r) for r in rows]


# ---------------------------------------------------------------------------
# CRUD: ScanSnapshot
# ---------------------------------------------------------------------------

def _row_to_snapshot(row: sqlite3.Row) -> ScanEntry:
    return ScanEntry(
        id=row["id"],
        profile_name=row["profile_name"],
        model_name=row["model_name"],
        provider_name=row["provider_name"],
        status=row["status"],
        last_available_at=row["last_available_at"],
        next_check_at=row["next_check_at"],
        error_reason=row["error_reason"],
    )


def upsert_snapshot(profile: str, entries: list[ScanEntry]) -> None:
    init_db()
    conn = _get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for e in entries:
        conn.execute(
            """INSERT INTO scan_snapshot
               (profile_name, model_name, provider_name, status,
                last_available_at, next_check_at, error_reason, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_name, model_name, provider_name) DO UPDATE SET
                   status=excluded.status,
                   last_available_at=excluded.last_available_at,
                   next_check_at=excluded.next_check_at,
                   error_reason=excluded.error_reason,
                   updated_at=excluded.updated_at""",
            (
                profile,
                e.model_name,
                e.provider_name,
                e.status,
                e.last_available_at,
                e.next_check_at,
                e.error_reason,
                now,
            ),
        )
    conn.commit()


def get_snapshot(profile: str) -> list[ScanEntry]:
    init_db()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM scan_snapshot WHERE profile_name = ? ORDER BY model_name, provider_name",
        (profile,),
    ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


# ---------------------------------------------------------------------------
# CRUD: ActiveCombo
# ---------------------------------------------------------------------------

def _row_to_combo(row: sqlite3.Row) -> ActiveCombo:
    return ActiveCombo(
        profile_name=row["profile_name"],
        model_name=row["model_name"],
        provider_name=row["provider_name"],
        config_updated=bool(row["config_updated"]),
    )


def set_active_combo(profile: str, model: str, provider: str) -> None:
    init_db()
    conn = _get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """INSERT INTO active_combo (profile_name, model_name, provider_name, config_updated, updated_at)
           VALUES (?,?,?,0,?)
           ON CONFLICT(profile_name) DO UPDATE SET
               model_name=excluded.model_name,
               provider_name=excluded.provider_name,
               config_updated=0,
               updated_at=excluded.updated_at""",
        (profile, model, provider, now),
    )
    conn.commit()


def get_active_combo(profile: str) -> Optional[ActiveCombo]:
    init_db()
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM active_combo WHERE profile_name = ?", (profile,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_combo(row)


def list_active_combos() -> list[ActiveCombo]:
    init_db()
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM active_combo ORDER BY profile_name").fetchall()
    return [_row_to_combo(r) for r in rows]


# ---------------------------------------------------------------------------
# CRUD: SwitchHistory
# ---------------------------------------------------------------------------

def add_history(entry: SwitchHistoryEntry) -> None:
    init_db()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO switch_history
           (profile_name, from_model, from_provider, to_model, to_provider, reason, triggered_by)
           VALUES (?,?,?,?,?,?,?)""",
        (
            entry.profile_name,
            entry.from_model,
            entry.from_provider,
            entry.to_model,
            entry.to_provider,
            entry.reason,
            entry.triggered_by,
        ),
    )
    conn.commit()


def get_history(profile: str, limit: int = 50) -> list[SwitchHistoryEntry]:
    init_db()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM switch_history WHERE profile_name = ? ORDER BY created_at DESC LIMIT ?",
        (profile, limit),
    ).fetchall()
    result = []
    for r in rows:
        result.append(SwitchHistoryEntry(
            id=r["id"],
            profile_name=r["profile_name"],
            from_model=r["from_model"],
            from_provider=r["from_provider"],
            to_model=r["to_model"],
            to_provider=r["to_provider"],
            reason=r["reason"],
            triggered_by=r["triggered_by"],
            created_at=r["created_at"],
        ))
    return result


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    init_db()
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM switch_config").fetchone()[0]
    auto_on = conn.execute("SELECT COUNT(*) FROM switch_config WHERE auto_switch = 1").fetchone()[0]
    manual = conn.execute("SELECT COUNT(*) FROM switch_config WHERE manual_override = 1").fetchone()[0]
    return {
        "total_profiles": total,
        "auto_switch_on": auto_on,
        "manual_override": manual,
    }
