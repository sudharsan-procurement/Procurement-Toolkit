"""
Local configuration for the AI layer (provider choice + API keys).

Why this exists
---------------
The app must run on a locked-down corporate laptop with **no admin rights and no
local LLM installed**. So the AI brain has to be configurable without touching
code or environment: the user picks a provider and pastes a cloud API key on the
Settings page, and we persist that choice to a small JSON file in their home
directory (which they can always write to, no admin needed).

Two sources of truth, in priority order, for any secret:
    1. the saved config file  (written by the Settings page)
    2. an environment variable (handy for shared/CI deployments)

Nothing here imports Streamlit or any provider — it's plain stdlib so it can be
unit-tested and reused from the CLI/self-test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# One folder per user, created lazily. Lives in the home directory so it works
# without admin rights and never lands in the repo.
CONFIG_DIR = Path(os.environ.get("SMARTDOC_CONFIG_DIR") or (Path.home() / ".smartdoc"))
CONFIG_PATH = CONFIG_DIR / "config.json"

# Default provider preference and model names. "auto" = detect Ollama, else
# Gemini, else the local rules engine.
DEFAULTS = {
    # auto | ollama | gemini | openai | github | claude | local
    "provider": "auto",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    # OpenAI-compatible: OpenAI, OpenRouter, Groq, Mistral, SiliconFlow, …
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "openai_base_url": "https://api.openai.com/v1",
    # GitHub Models (authenticated with a GitHub PAT).
    "github_token": "",
    "github_model": "openai/gpt-4o-mini",
    "github_base_url": "https://models.github.ai/inference",
    "anthropic_api_key": "",
    "anthropic_model": "claude-haiku-4-5-20251001",
    "ollama_model": "llama3.1",
    "ollama_host": "http://localhost:11434",
    # Access control (only effective when Google Sign-In is configured in
    # .streamlit/secrets.toml). require_login gates the whole app; admin_emails
    # (comma-separated) restricts the Usage page.
    "require_login": False,
    "admin_emails": "",
}

# Which environment variable backs each provider's API key, used as a fallback
# when the config file doesn't carry the secret. The first hit wins. The
# OpenAI-compatible provider accepts several common vendor env names so a key
# already exported for Groq/OpenRouter/etc. is picked up automatically.
ENV_KEYS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
               "MISTRAL_API_KEY", "SILICONFLOW_API_KEY", "TOGETHER_API_KEY",
               "DEEPSEEK_API_KEY"),
    "github": ("GITHUB_MODELS_TOKEN", "GITHUB_TOKEN"),
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
}

# Map a provider id to the config field that stores its key.
_KEY_FIELD = {
    "gemini": "gemini_api_key",
    "openai": "openai_api_key",
    "github": "github_token",
    "claude": "anthropic_api_key",
}


def load_settings() -> dict:
    """Return the saved settings merged over the defaults (defaults fill gaps)."""
    data = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update({k: v for k, v in saved.items() if v is not None})
    except Exception:
        # A corrupt config must never crash the app — fall back to defaults.
        pass
    return data


def save_settings(settings: dict) -> Path:
    """Persist settings to the per-user config file. Returns the path written.

    Only known keys are stored (so a stray UI value can't bloat the file). The
    file is written with owner-only permissions where the OS supports it, since
    it can hold API keys.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    clean = {k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}
    CONFIG_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    try:
        # 0o600 = read/write for the owner only. No-op / best-effort on Windows.
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
    return CONFIG_PATH


def get_api_key(provider: str, settings: dict | None = None) -> str:
    """Resolve a provider's API key: saved config first, then env vars."""
    settings = settings if settings is not None else load_settings()
    field = _KEY_FIELD.get(provider)
    if field:
        saved = (settings.get(field) or "").strip()
        if saved:
            return saved
    for env_name in ENV_KEYS.get(provider, ()):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    return ""


def key_source(provider: str, settings: dict | None = None) -> str:
    """Where the key comes from: 'config', 'environment', or '' (none). For UI."""
    settings = settings if settings is not None else load_settings()
    field = _KEY_FIELD.get(provider)
    if field and (settings.get(field) or "").strip():
        return "config"
    for env_name in ENV_KEYS.get(provider, ()):
        if os.environ.get(env_name, "").strip():
            return "environment"
    return ""
