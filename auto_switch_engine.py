"""provider-auto-switch: Priority resolution engine.

Core concepts:
- Two independent priority dimensions: models × providers
- Per-model provider overrides (model_providers) — each model can have its own
  provider priority list; unspecified models fall back to global provider_priority
- Per-provider model overrides (provider_models) — each provider can have its own
  model priority list; unspecified providers fall back to global model_priority
- Two-pass sorting: active combos first, limited as last resort
- Combo existence checked against scan snapshot data (not hardcoded)
"""

from __future__ import annotations

from typing import Optional

from auto_switch_db import (
    SwitchConfig,
    ScanEntry,
    add_history,
    get_active_combo,
    get_snapshot,
    set_active_combo,
    SwitchHistoryEntry,
)


def _get_providers_for_model(model: str, cfg: SwitchConfig) -> list[str]:
    """Provider priority for a specific model.

    Uses per-model override if available; otherwise falls back to global
    provider_priority. This is the generic mechanism — no model names are
    hardcoded, so it works with any provider/model combination.
    """
    if cfg.model_providers and model in cfg.model_providers:
        return cfg.model_providers[model]
    return cfg.provider_priority


def _get_models_for_provider(provider: str, cfg: SwitchConfig) -> list[str]:
    """Model priority for a specific provider.

    Uses per-provider override if available; otherwise falls back to global
    model_priority. Generic — works with any provider/model combination.
    """
    if cfg.provider_models and provider in cfg.provider_models:
        return cfg.provider_models[provider]
    return cfg.model_priority


def _combo_exists(model: str, provider: str, provider_models_set: dict[str, set[str]]) -> bool:
    """True if the model was found on this provider during the last scan.

    Uses scan data only — if the provider doesn't serve this model according
    to the snapshot, it's treated as non-existent and skipped. No fallback.
    """
    return model in provider_models_set.get(provider, set())


def find_next_combo(cfg: SwitchConfig, snaps: list[ScanEntry]) -> Optional[tuple[str, str]]:
    """Two-pass priority resolution.

    Pass 1 (active): Returns the highest-priority combo with status 'active'.
    Pass 2 (limited): If no active combo exists, returns the highest-priority
                      combo with status 'limited' (rate-limited, quota-hit).

    Returns None when no combo is usable at all.

    Generic algorithm — driven entirely by config lists:
    - model_priority / provider_priority (global)
    - model_providers (per-model overrides)
    - provider_models (per-provider overrides)
    - scan_snapshot data (combo existence check)
    """
    if not cfg.model_priority or not cfg.provider_priority:
        return None

    # Build lookup maps from scan snapshots
    combo_status: dict[tuple[str, str], str] = {}
    provider_models_set: dict[str, set[str]] = {}
    for s in snaps:
        combo_status[(s.model_name, s.provider_name)] = s.status
        provider_models_set.setdefault(s.provider_name, set()).add(s.model_name)

    if cfg.strategy == "model_first":
        return _resolve_model_first(cfg, combo_status, provider_models_set)
    else:
        return _resolve_provider_first(cfg, combo_status, provider_models_set)


def _resolve_model_first(
    cfg: SwitchConfig,
    combo_status: dict[tuple[str, str], str],
    provider_models_set: dict[str, set[str]],
) -> Optional[tuple[str, str]]:
    """Model-first resolution with two-pass (active → limited).

    For each model (in priority order), iterate its provider list (using
    per-model override if available, otherwise global) and find the first
    active combo. If none, repeat the same traversal looking for limited.
    """
    # Pass 1: active only
    for model in cfg.model_priority:
        providers = _get_providers_for_model(model, cfg)
        for provider in providers:
            if not _combo_exists(model, provider, provider_models_set):
                continue  # skip — this provider doesn't serve this model
            if combo_status.get((model, provider)) == "active":
                return (model, provider)
    # Pass 2: limited as last resort
    for model in cfg.model_priority:
        providers = _get_providers_for_model(model, cfg)
        for provider in providers:
            if not _combo_exists(model, provider, provider_models_set):
                continue
            if combo_status.get((model, provider)) == "limited":
                return (model, provider)
    return None


def _resolve_provider_first(
    cfg: SwitchConfig,
    combo_status: dict[tuple[str, str], str],
    provider_models_set: dict[str, set[str]],
) -> Optional[tuple[str, str]]:
    """Provider-first resolution with two-pass (active → limited).

    For each provider (in priority order), iterate its model list (using
    per-provider override if available, otherwise global) and find the first
    active combo. If none, repeat looking for limited.
    """
    # Pass 1: active only
    for provider in cfg.provider_priority:
        models = _get_models_for_provider(provider, cfg)
        for model in models:
            if not _combo_exists(model, provider, provider_models_set):
                continue
            if combo_status.get((model, provider)) == "active":
                return (model, provider)
    # Pass 2: limited as last resort
    for provider in cfg.provider_priority:
        models = _get_models_for_provider(provider, cfg)
        for model in models:
            if not _combo_exists(model, provider, provider_models_set):
                continue
            if combo_status.get((model, provider)) == "limited":
                return (model, provider)
    return None


def auto_switch(
    profile: str,
    cfg: SwitchConfig,
    snaps: list[ScanEntry],
    reason: str = "auto",
    triggered_by: str = "auto",
) -> Optional[dict]:
    """Execute auto-switch logic for a profile.

    Returns a dict describing the switch if one occurred, or None if no
    switch was needed/possible.

    Args:
        profile: Profile name
        cfg: Switch config for this profile
        snaps: Current scan snapshot entries
        reason: Why the switch was triggered (quota_limit, rate_limit, etc.)
        triggered_by: Who triggered it (auto, manual, scheduler)
    """
    if cfg.manual_override:
        return None  # User is in control

    # Get current active combo
    current = get_active_combo(profile)
    current_model = current.model_name if current else ""
    current_provider = current.provider_name if current else ""

    # Find the best available combo
    target = find_next_combo(cfg, snaps)
    if target is None:
        return {"switched": False, "reason": "No usable combo found"}

    new_model, new_provider = target

    # If same as current, nothing to do
    if new_model == current_model and new_provider == current_provider:
        return {"switched": False, "reason": "Already on best combo"}

    # Execute switch (in v1, just record — actual switch_model is called by caller)
    set_active_combo(profile, new_model, new_provider)

    # Record history
    add_history(SwitchHistoryEntry(
        profile_name=profile,
        from_model=current_model,
        from_provider=current_provider,
        to_model=new_model,
        to_provider=new_provider,
        reason=reason,
        triggered_by=triggered_by,
    ))

    return {
        "switched": True,
        "from_model": current_model,
        "from_provider": current_provider,
        "to_model": new_model,
        "to_provider": new_provider,
        "reason": reason,
    }


def check_recovery(
    profile: str,
    cfg: SwitchConfig,
    snaps: list[ScanEntry],
) -> Optional[dict]:
    """Check if a higher-priority combo has become available again.

    Called periodically (via cron or manual) after a switch.
    If a better combo (higher in priority order) is now active,
    returns the switch info. Otherwise returns None.
    """
    best = find_next_combo(cfg, snaps)
    if best is None:
        return None

    current = get_active_combo(profile)
    if current is None:
        return None

    # Is the best combo different from AND better than the current one?
    if best[0] == current.model_name and best[1] == current.provider_name:
        return None  # Already on best

    # It's better — switch back
    return auto_switch(
        profile, cfg, snaps,
        reason="recovery",
        triggered_by="scheduler",
    )


def _scan_provider_models(profile_name: str) -> dict[str, list[str]]:
    """Discover available models from profile's configured providers.

    For each provider defined in the profile's config, calls its
    /v1/models endpoint (OpenAI-compatible) to get the model list.

    Returns {provider_name: [model1, model2, ...]}.
    Falls back to the configured model if the API call fails.
    """
    import requests
    import yaml
    from pathlib import Path

    # Discover profile config
    hermes_home = Path.home() / ".hermes"
    if profile_name == "default":
        config_path = hermes_home / "config.yaml"
    else:
        config_path = hermes_home / "profiles" / profile_name / "config.yaml"

    if not config_path.exists():
        return {}

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    result: dict[str, list[str]] = {}

    # 1. Built-in providers from the model config
    model_cfg = cfg.get("model", {})
    provider = model_cfg.get("provider", "")
    base_url = model_cfg.get("base_url", "")
    default_model = model_cfg.get("default", "")

    # Try scanning the current provider
    if provider and base_url:
        try:
            url = f"{base_url.rstrip('/')}/models"
            resp = requests.get(url, timeout=10)
            if resp.ok:
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                if models:
                    result[provider] = models
        except Exception:
            pass

    # 2. Custom providers
    for cp in cfg.get("custom_providers", []):
        name = cp.get("name", "")
        url = cp.get("base_url", "")
        api_key = cp.get("api_key", "")
        if name and url:
            try:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = requests.get(f"{url.rstrip('/')}/models",
                                    headers=headers, timeout=10)
                if resp.ok:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", [])]
                    if models:
                        result[name] = models
            except Exception:
                # Fallback: just use the configured model
                fallback = cp.get("model", "")
                if fallback and name:
                    result[name] = [fallback]

    # 3. If no providers scanned, try known Hermes providers
    if not result:
        known = {
            "opencode-zen": "https://opencode.ai/zen/v1",
            "opencode-go": "https://opencode.ai/zen/go/v1",
            "deepseek": "https://api.deepseek.com",
        }
        for pname, purl in known.items():
            try:
                resp = requests.get(f"{purl}/models", timeout=10)
                if resp.ok:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", [])]
                    if models:
                        result[pname] = models
            except Exception:
                pass

    # 4. Always include the default model as fallback
    if default_model and provider:
        if provider not in result or default_model not in result.get(provider, []):
            result.setdefault(provider, []).append(default_model)

    return result


ERROR_PATTERNS = {
    "quota_limit": [
        "usage limit", "quota", "limit reached", "insufficient balance",
        "GoUsageLimitError", "billing", "exceeded your",
    ],
    "rate_limit": [
        "429", "rate limit", "too many requests", "retry after",
        "rate_limit", "rate_limited",
    ],
    "service_error": [
        "503", "502", "service unavailable", "overloaded",
        "internal server error", "bad gateway",
    ],
    "auth_error": [
        "401", "unauthorized", "invalid api key", "authentication",
    ],
    "connection_error": [
        "connection error", "connection refused", "timeout",
        "name or service not known", "dns", "resolve",
    ],
}


def _detect_error_type(error_str: str) -> str:
    """Detect error category from the error message string."""
    error_lower = error_str.lower()
    for etype, patterns in ERROR_PATTERNS.items():
        for pat in patterns:
            if pat.lower() in error_lower:
                return etype
    return "unknown"


def handle_api_error(
    profile_name: str,
    provider: str,
    model: str,
    error_str: str,
) -> dict:
    """Handle an API error detected by the post_api_request hook.

    1. Detects error type (quota, rate limit, etc.)
    2. Marks the current combo as limited in the snapshot
    3. Finds the next best combo via auto_switch()
    4. Returns result dict with 'success' key

    Returns:
        {"success": True, "switched": True, "to_model": ..., ...} on switch
        {"success": False, "reason": "..."} on failure/no switch needed
    """
    from auto_switch_db import (
        get_config, get_snapshot, upsert_snapshot, ScanEntry,
    )

    cfg = get_config(profile_name)
    if cfg is None:
        return {"success": False, "reason": "No config for profile"}
    if cfg.manual_override:
        return {"success": False, "reason": "Manual override active"}

    # Mark the current combo as limited in snapshot
    error_type = _detect_error_type(error_str)

    # Update snapshot: mark this combo as limited
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    cooldown = timedelta(minutes=5)  # 5 minute cooldown for rate-limited

    if error_type in ("quota_limit", "rate_limit"):
        upsert_snapshot(profile_name, [
            ScanEntry(
                profile_name=profile_name,
                model_name=model,
                provider_name=provider,
                status="limited",
                last_available_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                next_check_at=(now + cooldown).strftime("%Y-%m-%dT%H:%M:%SZ"),
                error_reason=error_type,
            )
        ])

    # Read snapshot and try to switch
    snaps = get_snapshot(profile_name)
    result = auto_switch(profile_name, cfg, snaps, reason=error_type, triggered_by="auto")

    if result and result.get("switched"):
        return {
            "success": True,
            "switched": True,
            "to_model": result.get("to_model"),
            "to_provider": result.get("to_provider"),
            "from_model": result.get("from_model"),
            "from_provider": result.get("from_provider"),
            "reason": error_type,
        }

    return {"success": False, "reason": result.get("reason", "No switch needed") if result else "Unknown"}
