"""provider-auto-switch — Hermes plugin hooks.

Registers post_api_request hook to intercept API errors and trigger
automatic model switching.

This is loaded by Hermes as a traditional plugin (via __init__.py + register()).
The dashboard component lives under dashboard/.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Ensure plugin root is on sys.path for db/switch_engine imports
_plugin_root = Path(__file__).resolve().parent
if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

from auto_switch_db import init_db

log = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register plugin hooks (called by Hermes plugin loader)."""
    init_db()
    ctx.register_hook("post_api_request", _on_post_api_request)
    log.info("provider-auto-switch hooks registered")


async def _on_post_api_request(
    *,
    task_id: str = "",
    session_id: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    model: str = "",
    api_call_count: int = 0,
    response: Any = None,
    assistant_message: Any = None,
    usage: Any = None,
    error: Any = None,
    **_: Any,
) -> None:
    """Post-API-request hook — intercept errors and trigger auto-switch.

    This fires after every LLM API call. If the call had an error,
    we try to detect the profile from the session and trigger a switch.
    """
    if not error and not assistant_message:
        return  # No error and no assistant message — nothing to do

    if not error:
        return  # Only act on errors

    error_str = str(error)
    log.debug("post_api_request error: provider=%s model=%s error=%s", provider, model, error_str)

    # Determine profile_name from session_id
    profile_name = _resolve_profile(session_id, task_id, provider)
    if not profile_name:
        return

    # Mark the error and trigger auto-switch (run in executor to avoid blocking)
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _handle_error, profile_name, provider, model, error_str)


def _resolve_profile(session_id: str, task_id: str, provider: str) -> str | None:
    """Resolve the profile name from a session or task context.

    Tries to read the session metadata from the Hermes session store,
    or falls back to detecting from the provider name.
    """
    # Strategy 1: Try reading from session store
    try:
        from hermes_cli.hermes_state import SessionDB
        hermes_home = __import__("hermes_constants", fromlist=["get_hermes_home"]).get_hermes_home()
        sdb = SessionDB(hermes_home)
        meta = sdb.get_session_meta(session_id)
        if meta and meta.profile:
            return meta.profile
    except Exception:
        pass

    # Strategy 2: Try scanning home profiles for matching provider config
    try:
        from pathlib import Path
        import os
        import yaml
        hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))

        def _check_profile(cfg: dict) -> bool:
            """Check if a profile's config references this provider."""
            # Check model.provider
            m = cfg.get("model", {})
            if isinstance(m, dict) and m.get("provider") == provider:
                return True
            # Check custom_providers entries
            for cp in cfg.get("custom_providers", []):
                if isinstance(cp, dict) and cp.get("name") == provider:
                    return True
            return False

        profiles_dir = hermes_home / "profiles"
        if profiles_dir.is_dir():
            for d in sorted(profiles_dir.iterdir()):
                cfg_path = d / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path) as f:
                        cfg = yaml.safe_load(f) or {}
                    if _check_profile(cfg):
                        return d.name
        # Check default
        default_cfg = hermes_home / "config.yaml"
        if default_cfg.exists():
            with open(default_cfg) as f:
                cfg = yaml.safe_load(f) or {}
            if _check_profile(cfg):
                return "default"
    except Exception:
        pass

    return None


def _handle_error(profile_name: str, provider: str, model: str, error_str: str) -> None:
    """Synchronous error handler — runs in executor thread."""
    try:
        from auto_switch_engine import handle_api_error
        result = handle_api_error(profile_name, provider, model, error_str)
        if result and result.get("success"):
            log.info("Auto-switched %s due to error: %s", profile_name, error_str)
        elif result:
            log.info("Auto-switch check for %s: %s", profile_name, result.get("reason"))
    except Exception as e:
        log.error("Error in auto-switch handler: %s", e)
