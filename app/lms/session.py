"""Private storage-state persistence and conservative session checks."""
import json
import os
import tempfile
import stat
from pathlib import Path
from urllib.parse import urlsplit
from app.lms.base import LMSError


def read_state(path: Path):
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise LMSError("Session state must be a private regular file (chmod 600). Run lms-auth again if needed.")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get("cookies"), list) or not isinstance(value.get("origins"), list):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise LMSError("Saved session state is unreadable or malformed; run lms-auth again.") from None


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".bahria-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def login_page(page) -> bool:
    # Generic form semantics, not guessed Bahria selectors. Check embedded frames too.
    for frame in page.frames:
        fields = frame.locator('input[type="password"]')
        if any(fields.nth(i).is_visible() for i in range(fields.count())):
            return True
    segments = urlsplit(page.url).path.lower().replace(".", "/").split("/")
    return any(part in {"login", "signin", "sign-in", "authenticate"} for part in segments)


def session_status(page, portal_url: str) -> str:
    if login_page(page):
        return "reauthentication required (login page detected)"
    if urlsplit(page.url).netloc != urlsplit(portal_url).netloc:
        return "authentication unverified (redirected outside configured portal)"
    return "session appears usable; authentication is not verified until portal-specific markers are inspected"


def browser_options(headless: bool) -> dict:
    """Use an existing root-owned Chrome SUID helper without changing host policy."""
    options = {"headless": headless, "chromium_sandbox": True}
    helper = Path("/opt/google/chrome/chrome-sandbox")
    try:
        info = helper.stat()
        if (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and info.st_mode & stat.S_ISUID and not info.st_mode & 0o022):
            options["env"] = {**os.environ, "CHROME_DEVEL_SANDBOX": str(helper)}
    except OSError:
        pass
    return options
