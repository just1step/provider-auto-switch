"""provider-auto-switch — Dashboard plugin backend API routes.

Mounted at /api/plugins/provider-auto-switch/ by the dashboard plugin system.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Ensure the plugin root directory is on sys.path for imports of db, switch_engine
_plugin_root = Path(__file__).resolve().parent.parent
if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

from auto_switch_db import (
    SwitchConfig, ScanSnapshot, SwitchHistory,
    get_config, upsert_config, get_active_combo,
    get_snapshots, get_history, init_db,
    get_all_configs, list_profiles_from_config,
)
from auto_switch_engine import (
    scan_provider_models, auto_switch, execute_switch, check_recovery,
)

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConfigUpdate(BaseModel):
    strategy: Optional[str] = None
    auto_switch: Optional[bool] = None
    manual_override: Optional[bool] = None
    model_priority: Optional[list[str]] = None
    provider_priority: Optional[list[str]] = None
    scan_interval: Optional[int] = None


class ManualSwitch(BaseModel):
    model: str
    provider: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/profiles")
async def list_profiles():
    """List all profiles with their current config and active combo."""
    init_db()
    return {"profiles": get_all_configs()}


@router.post("/{profile}/scan")
async def scan_profile(profile: str):
    """Scan all providers for a profile."""
    stats = scan_provider_models(profile)
    return {"profile": profile, "stats": stats}


@router.get("/{profile}/config")
async def get_profile_config(profile: str):
    """Get the switch config for a profile."""
    cfg = get_config(profile)
    if not cfg:
        from dataclasses import asdict
        from auto_switch_db import SwitchConfig as SC
        cfg = SC(profile_name=profile)
        upsert_config(cfg)
    from dataclasses import asdict
    return {"profile": profile, "config": asdict(cfg)}


@router.put("/{profile}/config")
async def update_profile_config(profile: str, update: ConfigUpdate):
    """Update the switch config for a profile."""
    cfg = get_config(profile)
    from dataclasses import asdict
    from auto_switch_db import SwitchConfig as SC
    if not cfg:
        cfg = SC(profile_name=profile)

    if update.strategy is not None:
        cfg.strategy = update.strategy
    if update.auto_switch is not None:
        cfg.auto_switch = update.auto_switch
    if update.manual_override is not None:
        cfg.manual_override = update.manual_override
    if update.model_priority is not None:
        cfg.model_priority = update.model_priority
    if update.provider_priority is not None:
        cfg.provider_priority = update.provider_priority
    if update.scan_interval is not None:
        cfg.scan_interval = update.scan_interval

    upsert_config(cfg)
    return {"profile": profile, "config": asdict(cfg)}


@router.get("/{profile}/snapshot")
async def get_profile_snapshot(profile: str):
    """Get the scan snapshot for a profile."""
    snaps = get_snapshots(profile)
    # Group by model for the UI
    from collections import defaultdict
    by_model: dict[str, list] = defaultdict(list)
    for s in snaps:
        by_model[s.model_name].append({
            "provider": s.provider_name,
            "status": s.status,
            "last_available_at": s.last_available_at,
            "next_check_at": s.next_check_at,
            "error_reason": s.error_reason,
        })
    return {"profile": profile, "snapshots": dict(by_model)}


@router.post("/{profile}/switch")
async def manual_switch(profile: str, req: ManualSwitch):
    """Manually switch a profile to a specific model+provider."""
    # Manual switch sets manual_override flag
    cfg = get_config(profile)
    from auto_switch_db import SwitchConfig as SC
    if not cfg:
        cfg = SC(profile_name=profile)
    cfg.manual_override = True
    upsert_config(cfg)

    result = execute_switch(profile, req.model, req.provider, reason="manual", triggered_by="manual")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Switch failed"))
    return {"profile": profile, "result": result}


@router.post("/{profile}/check-recovery")
async def recovery_check(profile: str):
    """Check if failed models/providers have recovered."""
    result = check_recovery(profile)
    return {"profile": profile, "result": result}


@router.get("/{profile}/history")
async def get_switch_history(profile: str, limit: int = 50):
    """Get switch history for a profile."""
    return {"profile": profile, "history": get_history(profile, limit)}


@router.get("/stats")
async def get_global_stats():
    """Get global overview stats for all profiles."""
    profiles = get_all_configs()
    total = len(profiles)
    auto_enabled = 0
    manual_override = 0
    unhealthy = 0
    for p in profiles:
        if p.get("config") and p["config"].get("auto_switch"):
            auto_enabled += 1
        if p.get("config") and p["config"].get("manual_override"):
            manual_override += 1
        combo = p.get("active_combo")
        if not combo:
            unhealthy += 1
    return {
        "total_profiles": total,
        "auto_switch_enabled": auto_enabled,
        "manual_override_active": manual_override,
        "unhealthy": unhealthy,
    }
