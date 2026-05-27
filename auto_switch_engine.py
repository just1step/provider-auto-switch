"""provider-auto-switch — Core switching engine.

Provider scanning, matching algorithm, and switch execution via
hermes_cli.model_switch.switch_model().
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml
import requests

from auto_switch_db import (
    ScanSnapshot, SwitchConfig, SwitchHistory,
    upsert_snapshot, clear_snapshots, get_snapshots,
    get_config, upsert_config, get_active_combo, set_active_combo,
    add_history, list_profiles_from_config, get_all_configs,
    init_db,
)

log = logging.getLogger(__name__)

# How long to wait before checking a failed model again (seconds)
RECOVERY_CHECK_INTERVAL = 1800  # 30 minutes

# HTTP request timeout for provider API calls
PROVIDER_API_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Provider scanning
# ---------------------------------------------------------------------------

def _load_profile_config(profile_name: str) -> dict:
    """Load a profile's config.yaml and return its model section + providers."""
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    cfg_path = hermes_home / "profiles" / profile_name / "config.yaml"
    if not cfg_path.exists():
        cfg_path = hermes_home / "config.yaml"  # default profile fallback
    if not cfg_path.exists():
        return {}
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def _get_provider_base_url(provider: str, profile_cfg: dict) -> Optional[str]:
    """Resolve the base URL for a provider from config."""
    # 1. Check custom_providers
    custom = profile_cfg.get("custom_providers", [])
    if isinstance(custom, list):
        for cp in custom:
            if isinstance(cp, dict) and cp.get("name") == provider:
                return cp.get("base_url", "").rstrip("/")
    # 2. Check providers dict
    provs = profile_cfg.get("providers", {})
    if isinstance(provs, dict) and provider in provs:
        pdef = provs[provider]
        if isinstance(pdef, dict):
            return pdef.get("base_url", "").rstrip("/")
    # 3. Known built-in providers
    BUILTIN = {
        "opencode-zen": "https://opencode.ai/zen/v1",
        "opencode-go": "https://opencode.ai/zen/go/v1",
        "opencode": "https://opencode.ai/zen/v1",
        "deepseek": "https://api.deepseek.com",
        "openai-codex": None,  # no public /v1/models endpoint
        "openrouter": "https://openrouter.ai/api/v1",
    }
    return BUILTIN.get(provider)


def _discover_providers(profile_name: str) -> list[dict]:
    """Discover providers and their models for a profile.

    Returns list of {provider, model, base_url}.
    """
    cfg = _load_profile_config(profile_name)
    model_cfg = cfg.get("model", {})
    current_provider = model_cfg.get("provider", "")
    current_model = model_cfg.get("default", "")
    custom_provs = cfg.get("custom_providers", [])

    # Collect all providers this profile might use
    providers_to_check = set()
    providers_to_check.add(current_provider)

    # Add providers from custom_providers
    if isinstance(custom_provs, list):
        for cp in custom_provs:
            if isinstance(cp, dict):
                providers_to_check.add(cp.get("name", ""))

    # Add providers from switch_config
    sc = get_config(profile_name)
    if sc:
        providers_to_check.update(sc.provider_priority)

    # Add other known built-in providers
    providers_to_check.update(["opencode-zen", "opencode-go", "deepseek"])

    result = []
    for provider in sorted(providers_to_check):
        if not provider:
            continue
        base_url = _get_provider_base_url(provider, cfg)
        if not base_url:
            log.warning("No base_url for provider '%s' (profile %s)", provider, profile_name)
            result.append({"provider": provider, "models": [], "base_url": ""})
            continue
        models = _fetch_models(provider, base_url)
        result.append({"provider": provider, "models": models, "base_url": base_url})

    return result


def _fetch_models(provider: str, base_url: str) -> list[str]:
    """Call GET {base_url}/models to discover available model names.

    Returns list of model ID strings, or empty on failure.
    """
    url = f"{base_url}/models"
    try:
        resp = requests.get(url, timeout=PROVIDER_API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("data", [])
        if isinstance(raw, list):
            return sorted(set(
                m.get("id", "") for m in raw if isinstance(m, dict)
            ))
        elif isinstance(raw, dict):
            # Some providers return a dict keyed by model name
            return sorted(raw.keys())
    except requests.RequestException as e:
        log.warning("Failed to scan %s %s: %s", provider, url, e)
    except (ValueError, TypeError) as e:
        log.warning("Failed to parse model list from %s: %s", provider, e)
    return []


def scan_provider_models(profile_name: str) -> dict:
    """Scan all providers for a profile and update the snapshot table.

    Returns summary dict.
    """
    discovered = _discover_providers(profile_name)
    clear_snapshots(profile_name)

    model_set = set()
    stats = {"active": 0, "unavailable": 0, "total": 0, "providers": []}

    for entry in discovered:
        provider = entry["provider"]
        models = entry["models"]
        stats["providers"].append({"name": provider, "count": len(models)})

        if not models:
            # Provider unreachable — mark all previously known models as unavailable
            pass

        for model in models:
            # Check if this model is known to be limited/unavailable
            prev = get_active_combo(profile_name)
            snap = ScanSnapshot(
                profile_name=profile_name,
                model_name=model,
                provider_name=provider,
                status="active",
                last_available_at=datetime.utcnow().isoformat(),
            )
            upsert_snapshot(snap)
            model_set.add(model)
            stats["active"] += 1
            stats["total"] += 1

    return stats


# ---------------------------------------------------------------------------
# Matching algorithm
# ---------------------------------------------------------------------------

def _find_next_combo(profile_name: str, cfg: SwitchConfig) -> Optional[tuple[str, str]]:
    """Find the next active model+provider combo per the strategy.

    Returns (model_name, provider_name) or None if none available.
    """
    snaps = get_snapshots(profile_name)
    # Build lookup: (model, provider) -> status
    lookup = {}
    for s in snaps:
        lookup[(s.model_name, s.provider_name)] = s.status

    # Build set of which models each provider supports
    provider_models: dict[str, set[str]] = {}
    model_providers: dict[str, set[str]] = {}
    for s in snaps:
        provider_models.setdefault(s.provider_name, set()).add(s.model_name)
        model_providers.setdefault(s.model_name, set()).add(s.provider_name)

    if cfg.strategy == "model_first":
        for model in cfg.model_priority:
            for provider in cfg.provider_priority:
                status = lookup.get((model, provider), "unavailable")
                if status == "active" or status == "limited":
                    return (model, provider)
        # Fallback: try second model with all providers
        for model in cfg.model_priority[1:]:
            for provider in cfg.provider_priority:
                status = lookup.get((model, provider), "unavailable")
                if status == "active":
                    return (model, provider)
    else:
        # provider_first
        for provider in cfg.provider_priority:
            for model in cfg.model_priority:
                status = lookup.get((model, provider), "unavailable")
                if status == "active" or status == "limited":
                    return (model, provider)
        for provider in cfg.provider_priority[1:]:
            for model in cfg.model_priority:
                status = lookup.get((model, provider), "unavailable")
                if status == "active":
                    return (model, provider)

    return None


# ---------------------------------------------------------------------------
# Switch execution
# ---------------------------------------------------------------------------

def execute_switch(
    profile_name: str,
    target_model: str,
    target_provider: str,
    reason: str = "auto",
    triggered_by: str = "auto",
) -> dict:
    """Execute a model switch using hermes_cli.model_switch.switch_model().

    Returns result dict with success/error.
    """
    try:
        from hermes_cli.model_switch import switch_model
        from hermes_cli.config import get_compatible_custom_providers

        cfg = _load_profile_config(profile_name)
        model_cfg = cfg.get("model", {})
        current_model = model_cfg.get("default", "")
        current_provider = model_cfg.get("provider", "")
        current_base_url = model_cfg.get("base_url", "")
        current_api_key = model_cfg.get("api_key", "")
        custom_provs = get_compatible_custom_providers(cfg)

        result = switch_model(
            raw_input=target_model,
            current_provider=current_provider,
            current_model=current_model,
            current_base_url=current_base_url,
            current_api_key=current_api_key,
            is_global=True,
            explicit_provider=target_provider,
            user_providers=cfg.get("providers"),
            custom_providers=custom_provs,
        )

        if result.success:
            set_active_combo(profile_name, target_model, target_provider)
            h = SwitchHistory(
                profile_name=profile_name,
                from_model=current_model,
                from_provider=current_provider,
                to_model=target_model,
                to_provider=target_provider,
                reason=reason,
                triggered_by=triggered_by,
            )
            add_history(h)
            log.info("Switched %s: %s/%s -> %s/%s (%s)",
                      profile_name, current_provider, current_model,
                      target_provider, target_model, reason)
            return {"success": True, "from": {"model": current_model, "provider": current_provider},
                    "to": {"model": target_model, "provider": target_provider}, "reason": reason}
        else:
            log.error("Switch failed for %s: %s", profile_name, result.error_message)
            return {"success": False, "error": result.error_message}

    except ImportError as e:
        log.error("Cannot import switch_model: %s", e)
        return {"success": False, "error": f"Import error: {e}"}
    except Exception as e:
        log.error("Switch failed: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Auto-switch trigger
# ---------------------------------------------------------------------------

def auto_switch(profile_name: str, reason: str = "auto", triggered_by: str = "auto") -> dict:
    """Check if a profile needs switching, and execute if needed.

    Returns result dict.
    """
    cfg = get_config(profile_name)
    if not cfg:
        # Auto-create default config for this profile
        cfg = SwitchConfig(profile_name=profile_name)
        upsert_config(cfg)

    if cfg.manual_override:
        log.info("Skip auto-switch for %s: manual override active", profile_name)
        return {"success": False, "reason": "manual_override"}

    if not cfg.auto_switch:
        log.info("Skip auto-switch for %s: auto-switch disabled", profile_name)
        return {"success": False, "reason": "auto_switch_disabled"}

    # Scan providers first to get current state
    scan_provider_models(profile_name)

    # Find the best combo
    combo = _find_next_combo(profile_name, cfg)
    if not combo:
        log.warning("No available combo for %s", profile_name)
        return {"success": False, "reason": "no_available_combo"}

    target_model, target_provider = combo
    current = get_active_combo(profile_name)

    if current and current["model_name"] == target_model and current["provider_name"] == target_provider:
        return {"success": True, "reason": "already_active", "combo": combo}

    return execute_switch(profile_name, target_model, target_provider, reason, triggered_by)


# ---------------------------------------------------------------------------
# Recovery detection
# ---------------------------------------------------------------------------

def check_recovery(profile_name: str) -> dict:
    """Check if previously failed models/providers have recovered.

    Returns list of recovered items.
    """
    cfg = get_config(profile_name)
    if not cfg:
        return {"recovered": []}

    # Re-scan providers
    scan_provider_models(profile_name)
    combo = _find_next_combo(profile_name, cfg)

    current = get_active_combo(profile_name)
    if combo and current:
        target_model, target_provider = combo
        if target_model != current["model_name"] or target_provider != current["provider_name"]:
            # Better option available — switch back
            result = execute_switch(profile_name, target_model, target_provider, "recovery", "scheduler")
            return {"recovered": [{"model": target_model, "provider": target_provider}], "switch_result": result}

    return {"recovered": []}


# ---------------------------------------------------------------------------
# Error trigger from hooks
# ---------------------------------------------------------------------------

def handle_api_error(profile_name: str, provider: str, model: str, error_str: str) -> Optional[dict]:
    """Handle an API error from the post_api_request hook.

    Marks the model/provider as limited/unavailable and triggers auto-switch.
    """
    # Determine reason
    reason = "unknown"
    el = error_str.lower()
    if "gousagelimiterror" in el or "quota" in el or "usage limit" in el or "limit reached" in el:
        reason = "quota_limit"
    elif "429" in el or "rate limit" in el or "too many requests" in el or "retry after" in el:
        reason = "rate_limit"
    elif "503" in el or "502" in el or "service unavailable" in el or "overloaded" in el:
        reason = "service_error"
    elif "connectionerror" in el or "timeout" in el or "connection refused" in el:
        reason = "connection_error"

    # Mark in snapshot
    snap = ScanSnapshot(
        profile_name=profile_name,
        model_name=model,
        provider_name=provider,
        status="limited" if reason in ("quota_limit", "rate_limit") else "unavailable",
        next_check_at=(datetime.utcnow() + timedelta(seconds=RECOVERY_CHECK_INTERVAL)).isoformat(),
        error_reason=reason,
    )
    upsert_snapshot(snap)

    # Trigger auto-switch
    return auto_switch(profile_name, reason, "auto")
