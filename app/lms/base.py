"""Configuration shared by manual LMS browser commands."""
import os
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class LMSError(Exception):
    """Safe, user-facing browser error."""


def validate_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise LMSError("Set BAHRIA_PORTAL_URL to the exact HTTPS portal URL, without credentials or fragments.")
    return value


@dataclass(frozen=True)
class LMSSettings:
    portal_url: str
    state_path: Path
    profile_path: Path | None = None


def load_settings() -> LMSSettings:
    root = Path(__file__).resolve().parents[2]
    state_path = root / "credentials" / "bahria_storage_state.json"
    url = os.environ.get("BAHRIA_PORTAL_URL", "").strip()
    if not url:
        try:
            url = json.loads(state_path.with_name("bahria_portal.json").read_text())["portal_url"]
            if not isinstance(url, str):
                raise ValueError()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            raise LMSError("Saved portal configuration is invalid; set BAHRIA_PORTAL_URL explicitly.") from None
    return LMSSettings(url, state_path)
