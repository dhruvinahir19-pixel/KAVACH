"""
config.py — environment contract. Fail FAST and PRECISELY at boot: a missing secret
must never surface as a None at 09:45. Error messages contain variable NAMES, never values.
"""
import os

REQUIRED = (
    "NEON_DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TRIGGER_SECRET",           # shared secret for /trigger endpoints
)

# P3: Upstox token is the FALLBACK path (public fetch is primary, verified
# after close 2026-09-10). Empty is valid — morning.py falls back only if set.
OPTIONAL = (
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_TOKEN_GENERATED",   # YYYY-MM-DD, for T-30d expiry alerts
)


class ConfigError(RuntimeError):
    pass


def load(strict: bool = True) -> dict[str, str]:
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing and strict:
        raise ConfigError(
            "missing required environment variables: " + ", ".join(missing)
            + " (see .env.example; on Render set them as secret env vars)"
        )
    cfg = {k: os.environ.get(k, "").strip() for k in REQUIRED}
    cfg.update({k: os.environ.get(k, "").strip() for k in OPTIONAL})  # "" = unset
    return cfg
