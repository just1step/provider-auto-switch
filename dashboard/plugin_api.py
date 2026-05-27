"""provider-auto-switch: Dashboard plugin API routes."""

import sys
from pathlib import Path
from datetime import datetime

# Import sibling modules via sys.path
_plugin_root = Path(__file__).resolve().parent.parent
if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from auto_switch_db import (
    init_db,
    get_config,
    upsert_config,
    list_configs,
    SwitchConfig,
    get_snapshot,
    upsert_snapshot,
    get_active_combo,
    set_active_combo,
    list_active_combos,
    get_stats,
    get_history,
    add_history,
    SwitchHistoryEntry,
    ScanEntry,
)
from auto_switch_engine import find_next_combo, auto_switch, check_recovery

router = APIRouter()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ConfigUpdate(BaseModel):
    strategy: Optional[str] = None
    auto_switch: Optional[bool] = None
    manual_override: Optional[bool] = None
    model_priority: Optional[list[str]] = None
    provider_priority: Optional[list[str]] = None
    model_providers: Optional[dict[str, list[str]]] = None
    provider_models: Optional[dict[str, list[str]]] = None
    scan_interval: Optional[int] = None


class ManualSwitchBody(BaseModel):
    model: str
    provider: str
    reason: str = "manual"


class ScanResultItem(BaseModel):
    model: str
    provider: str
    status: str = "unknown"


# ---------------------------------------------------------------------------
# Profile discovery
# ---------------------------------------------------------------------------

def _discover_profiles() -> list[str]:
    """Discover all profiles by scanning ~/.hermes/profiles/."""
    hermes_home = Path.home() / ".hermes"
    profiles = ["default"]
    profiles_dir = hermes_home / "profiles"
    if profiles_dir.exists():
        for d in sorted(profiles_dir.iterdir()):
            if d.is_dir() and (d / "config.yaml").exists():
                profiles.append(d.name)
    return profiles


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/profiles")
def api_list_profiles():
    """List all profiles with their current active combo and config status."""
    init_db()
    profiles = _discover_profiles()
    combos = {c.profile_name: {"model_name": c.model_name, "provider_name": c.provider_name}
              for c in list_active_combos()}
    configs = {c.profile_name: c for c in list_configs()}

    result = []
    for p in profiles:
        cfg = configs.get(p)
        active_combo = combos.get(p)

        # If no active_combo in DB, try reading from profile's config.yaml
        if active_combo is None:
            combo_from_config = _read_active_combo_from_config(p)
            if combo_from_config:
                active_combo = combo_from_config

        result.append({
            "profile_name": p,
            "config": {
                "strategy": cfg.strategy if cfg else "model_first",
                "auto_switch": cfg.auto_switch if cfg else False,
                "manual_override": cfg.manual_override if cfg else False,
            } if cfg else None,
            "active_combo": {
                "model_name": active_combo["model_name"],
                "provider_name": active_combo["provider_name"],
            } if active_combo else None,
        })
    return {"profiles": result}


def _read_active_combo_from_config(profile: str) -> Optional[dict]:
    """Try to read the current model/provider from a profile's config.yaml."""
    import yaml
    hermes_home = Path.home() / ".hermes"
    if profile == "default":
        path = hermes_home / "config.yaml"
    else:
        path = hermes_home / "profiles" / profile / "config.yaml"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        model_cfg = cfg.get("model", {})
        model_name = model_cfg.get("default", "")
        provider_name = model_cfg.get("provider", "")
        if model_name and provider_name:
            return {"model_name": model_name, "provider_name": provider_name}
    except Exception:
        pass
    return None


@router.get("/stats")
def api_stats():
    """Global statistics across all profiles."""
    init_db()
    profiles = _discover_profiles()
    configs = {c.profile_name: c for c in list_configs()}
    auto_on = sum(1 for p in profiles if p in configs and configs[p].auto_switch)
    manual = sum(1 for p in profiles if p in configs and configs[p].manual_override)
    return {
        "total_profiles": len(profiles),
        "configured": len(configs),
        "auto_switch_on": auto_on,
        "manual_override": manual,
    }


@router.get("/{profile}/config")
def api_get_config(profile: str):
    """Get switch configuration for a profile."""
    init_db()
    cfg = get_config(profile)
    if cfg is None:
        # Return defaults
        cfg = SwitchConfig(profile_name=profile)
    return {
        "profile_name": cfg.profile_name,
        "strategy": cfg.strategy,
        "auto_switch": cfg.auto_switch,
        "manual_override": cfg.manual_override,
        "model_priority": cfg.model_priority,
        "provider_priority": cfg.provider_priority,
        "model_providers": cfg.model_providers,
        "provider_models": cfg.provider_models,
        "scan_interval": cfg.scan_interval,
    }


@router.put("/{profile}/config")
def api_update_config(profile: str, body: ConfigUpdate):
    """Update switch configuration for a profile."""
    init_db()
    cfg = get_config(profile) or SwitchConfig(profile_name=profile)

    if body.strategy is not None:
        cfg.strategy = body.strategy
    if body.auto_switch is not None:
        cfg.auto_switch = body.auto_switch
    if body.manual_override is not None:
        cfg.manual_override = body.manual_override
    if body.model_priority is not None:
        cfg.model_priority = body.model_priority
    if body.provider_priority is not None:
        cfg.provider_priority = body.provider_priority
    if body.model_providers is not None:
        cfg.model_providers = body.model_providers
    if body.provider_models is not None:
        cfg.provider_models = body.provider_models
    if body.scan_interval is not None:
        cfg.scan_interval = body.scan_interval

    upsert_config(cfg)
    return {"ok": True, "profile": profile}


@router.get("/{profile}/snapshot")
def api_get_snapshot(profile: str):
    """Get scan snapshot for a profile."""
    init_db()
    snaps = get_snapshot(profile)
    return {
        "profile": profile,
        "entries": [
            {
                "model": s.model_name,
                "provider": s.provider_name,
                "status": s.status,
                "last_available_at": s.last_available_at,
                "next_check_at": s.next_check_at,
                "error_reason": s.error_reason,
            }
            for s in snaps
        ],
    }


@router.post("/{profile}/scan")
def api_scan(profile: str):
    """Scan all available providers for their model lists.

    Updates the scan_snapshot table with discovered model×provider combos.
    Each combo is marked 'active' if found, 'unavailable' if not.
    """
    init_db()
    profiles = _discover_profiles()
    if profile not in profiles:
        raise HTTPException(404, f"Profile '{profile}' not found")

    # Try scanning real provider APIs
    from auto_switch_engine import _scan_provider_models

    # Discover from available providers
    # This reads the profile's config to find configured providers
    discovered = _scan_provider_models(profile)

    # Build entries
    entries = []
    for provider, models in discovered.items():
        for model in models:
            entries.append(ScanEntry(
                profile_name=profile,
                model_name=model,
                provider_name=provider,
                status="active",
                last_available_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            ))

    if entries:
        upsert_snapshot(profile, entries)

    return {
        "profile": profile,
        "scanned": len(entries),
        "entries": [
            {"model": e.model_name, "provider": e.provider_name, "status": e.status}
            for e in entries
        ],
    }


@router.post("/{profile}/switch")
def api_manual_switch(profile: str, body: ManualSwitchBody):
    """Manually switch a profile to a specific model+provider combination."""
    init_db()

    # Get current combo before switching
    current = get_active_combo(profile)

    # Set manual override
    cfg = get_config(profile) or SwitchConfig(profile_name=profile)
    cfg.manual_override = True
    upsert_config(cfg)

    # Record history
    add_history(SwitchHistoryEntry(
        profile_name=profile,
        from_model=current.model_name if current else "",
        from_provider=current.provider_name if current else "",
        to_model=body.model,
        to_provider=body.provider,
        reason=body.reason,
        triggered_by="manual",
    ))

    # Update active combo
    set_active_combo(profile, body.model, body.provider)

    return {
        "ok": True,
        "profile": profile,
        "model": body.model,
        "provider": body.provider,
    }


@router.post("/{profile}/check-recovery")
def api_check_recovery(profile: str):
    """Check if a higher-priority combo has recovered."""
    init_db()
    cfg = get_config(profile)
    if cfg is None:
        raise HTTPException(400, f"No config for profile '{profile}'")

    snaps = get_snapshot(profile)
    result = check_recovery(profile, cfg, snaps)
    if result is None:
        return {"recovered": False}
    return {"recovered": True, "switch": result}


@router.get("/{profile}/history")
def api_get_history(profile: str, limit: int = Query(50)):
    """Get switch history for a profile."""
    init_db()
    entries = get_history(profile, limit)
    return {
        "profile": profile,
        "entries": [
            {
                "id": e.id,
                "from_model": e.from_model,
                "from_provider": e.from_provider,
                "to_model": e.to_model,
                "to_provider": e.to_provider,
                "reason": e.reason,
                "triggered_by": e.triggered_by,
                "created_at": e.created_at,
            }
            for e in entries
        ],
    }
