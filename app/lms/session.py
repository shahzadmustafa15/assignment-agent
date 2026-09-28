"""Private storage-state persistence and conservative session checks."""
import json
import os
import tempfile
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from app.lms.base import LMSError


def read_state(path: Path):
    if not path.exists():
        return None
    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
            raise LMSError("Session state must be a private regular file (chmod 600). Run lms-auth again if needed.")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get("cookies"), list) or not isinstance(value.get("origins"), list):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise LMSError("Saved session state is unreadable or malformed; run lms-auth again.") from None


def save_state(path: Path, state: dict):
    private_directory(path.parent)
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


def private_directory(path: Path):
    """Do not follow a redirected authentication directory."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise LMSError("Authentication directories must not be symbolic links.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def prepare_profile(path: Path):
    private_directory(path.parent)
    private_directory(path)
    private_directory(path / "Default")
    preferences = path / "Default" / "Preferences"
    try:
        if preferences.is_symlink():
            raise ValueError()
        value = json.loads(preferences.read_text()) if preferences.exists() else {}
        # Disable Chromium's password saving and autofill before every launch.
        value["credentials_enable_service"] = False
        value.setdefault("profile", {})["password_manager_enabled"] = False
        value.setdefault("autofill", {}).update(profile_enabled=False, credit_card_enabled=False)
        save_state(preferences, value)
    except (ValueError, TypeError, AttributeError):
        raise LMSError("Browser profile preferences are invalid; existing profile was preserved.") from None


def has_session(settings):
    return settings.state_path.exists() or bool(
        settings.profile_path and (settings.profile_path / ".initialized").exists()
    )


@contextmanager
def playwright_session(factory):
    """Disable driver diagnostics that can print authenticated navigation URLs."""
    names = ('DEBUG', 'DEBUG_FILE', 'PWDEBUG')
    previous = {name: os.environ.pop(name) for name in names if name in os.environ}
    try:
        with factory() as playwright:
            yield playwright
    finally:
        os.environ.update(previous)


@contextmanager
def browser_context(playwright, settings, *, headless, state=None):
    """Keep legacy contexts compatible; student contexts use a dedicated profile.

    Callers hold the student's scan lock for the entire profile lifecycle.
    The JSON snapshot bootstraps a new profile once; it never replaces an
    established profile's local storage, IndexedDB or persistent cookies.
    """
    if settings.profile_path is None:
        browser = playwright.chromium.launch(**browser_options(headless))
        try:
            yield browser.new_context(storage_state=state, accept_downloads=False)
        finally:
            browser.close()
        return

    path = settings.profile_path
    prepare_profile(path)
    marker = path / ".initialized"
    if state is None and settings.state_path.exists():
        state = read_state(settings.state_path)
    # The child browser inherits this restrictive mask, including on crash.
    # These CLI operations are synchronous; do not invoke in a threaded server.
    previous_mask = os.umask(0o077)
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(path), accept_downloads=False,
            **browser_options(headless),
        )
        try:
            if not marker.exists():
                if state is not None:
                    context.set_storage_state(state)
                save_state(marker, {"version": 1})
            elif state is not None:
                # Chromium may discard session cookies across clean shutdowns.
                # Restore only missing session cookies, never overwrite newer ones.
                def key(cookie):
                    return (cookie['name'], cookie['domain'], cookie['path'],
                            cookie.get('partitionKey'))
                present = {key(cookie) for cookie in context.cookies()}
                missing = [cookie for cookie in state['cookies']
                           if cookie.get('expires') == -1 and key(cookie) not in present]
                if missing:
                    context.add_cookies(missing)
            yield context
        finally:
            context.close()
    finally:
        os.umask(previous_mask)
