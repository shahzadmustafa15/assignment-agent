"""Installed-app OAuth for reading and sending Gmail; credential contents never enter CLI output."""

import json
import os
import re
from pathlib import Path
import tempfile

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2

from app.config import GmailSettings
from app.gmail_retry import DEFAULT_MAX_RETRIES, execute_with_retry

SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.send")


class GmailError(RuntimeError):
    """Safe, actionable error without tokens, raw API responses, or email content."""


def _private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise GmailError("OAuth files must be regular local files, not symbolic links.")
    path.chmod(0o600)


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise GmailError("The OAuth directory must not be a symbolic link.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def save_token(settings: GmailSettings, credentials) -> None:
    _private_directory(settings.credentials_dir)
    if settings.token_path.is_symlink():
        raise GmailError("The token path must not be a symbolic link.")
    descriptor, name = tempfile.mkstemp(prefix=".gmail-token-", dir=settings.credentials_dir)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(credentials.to_json())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, settings.token_path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _saved_credentials(settings: GmailSettings):
    if not settings.token_path.exists():
        return None
    _private_directory(settings.credentials_dir)
    _private_file(settings.token_path)
    try:
        data = json.loads(settings.token_path.read_text())
        if set(data.get("scopes", [])) != set(SCOPES):
            raise GmailError("Saved authorization has unexpected scopes. Gmail read and send permissions now require explicit reauthorization: python -m app.main gmail-auth --force")
        return Credentials.from_authorized_user_info(data, scopes=SCOPES)
    except (ValueError, TypeError, AttributeError) as exc:
        raise GmailError("Saved OAuth token is malformed. Run gmail-auth --force to replace it.") from exc


def _request(*args, **kwargs):
    kwargs.setdefault("timeout", 20)
    return Request()(*args, **kwargs)


def authenticate(settings: GmailSettings, *, interactive=False, force=False):
    credentials = None if force else _saved_credentials(settings)
    if credentials is not None and credentials.valid:
        return credentials
    if credentials is not None and credentials.refresh_token:
        try:
            credentials.refresh(_request)
            save_token(settings, credentials)
            return credentials
        except RefreshError as exc:
            if not interactive:
                raise GmailError("Gmail authorization expired or was revoked. Run gmail-auth --force.") from exc
        except (TransportError, OSError) as exc:
            raise GmailError("Cannot refresh Gmail authorization. Check the network and retry.") from exc
    if not interactive:
        raise GmailError("Gmail is not authorized. Complete Google Cloud setup, then run gmail-auth.")
    if not settings.credentials_path.exists():
        raise GmailError(
            f"Missing desktop OAuth client file: {settings.credentials_path}. "
            "Complete the manual Google Cloud steps in docs/GMAIL_SETUP.md."
        )
    _private_directory(settings.credentials_dir)
    _private_file(settings.credentials_path)
    try:
        config = json.loads(settings.credentials_path.read_text())
        installed = config.get("installed", {})
        if (not installed.get("client_id") or not installed.get("client_secret")
                or installed.get("auth_uri") not in ("https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth")
                or installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
            raise GmailError("Expected an official Google Desktop app OAuth client JSON file.")
        flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES, autogenerate_code_verifier=True)
        # Bound token exchange HTTP requests; OAuth itself allows three minutes for consent.
        original_request = flow.oauth2session.request
        def bounded_request(*args, **kwargs):
            kwargs.setdefault("timeout", 20)
            return original_request(*args, **kwargs)
        flow.oauth2session.request = bounded_request
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, timeout_seconds=180,
            authorization_prompt_message="Opening Google's authorization page in your browser.",
            success_message="Gmail authorization completed. You may close this tab.",
            access_type="offline", prompt="consent", include_granted_scopes="false",
        )
        granted = credentials.granted_scopes or credentials.scopes
        if set(granted or []) != set(SCOPES):
            raise GmailError("Required Gmail read and send permissions were not granted. Run python -m app.main gmail-auth --force.")
        save_token(settings, credentials)
        return credentials
    except GmailError:
        raise
    except Exception as exc:
        # OAuth libraries may include secrets in exception messages. Never render them.
        raise GmailError("OAuth setup failed or was cancelled/timed out. Verify the Desktop client and test user, then retry gmail-auth.") from exc


def gmail_service(settings: GmailSettings, *, interactive=False, force=False):
    credentials = authenticate(settings, interactive=interactive, force=force)
    return build("gmail", "v1", http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=20)),
                 cache_discovery=False, static_discovery=True)


# Only recognized machine codes may be echoed; API messages/URLs are never printed.
SAFE_API_CODES = frozenset({
    "badRequest", "invalidArgument", "authError", "invalidCredentials", "forbidden",
    "insufficientPermissions", "accessNotConfigured", "domainPolicy", "dailyLimitExceeded",
    "rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError",
    "internalError", "notFound", "failedPrecondition", "serviceDisabled", "limitExceeded",
    "conditionNotMet", "notImplemented", "PERMISSION_DENIED", "UNAUTHENTICATED",
    "RESOURCE_EXHAUSTED", "INVALID_ARGUMENT", "NOT_FOUND", "FAILED_PRECONDITION",
    "INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED", "SERVICE_DISABLED",
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "API_KEY_SERVICE_BLOCKED",
})


def safe_message_id(value: str) -> str:
    return value if re.fullmatch(r"[a-fA-F0-9]{1,64}", value) else "[invalid-id]"


def _http_error_details(exc: HttpError) -> str:
    raw_status = getattr(exc.resp, "status", None)
    status = str(raw_status) if str(raw_status).isdigit() else "unknown"
    details = [f"HTTP {status}"]
    try:
        error = json.loads(exc.content).get("error", {})
        if not isinstance(error, dict):
            return "; ".join(details)
        code = error.get("code")
        if type(code) is int and 100 <= code <= 599:
            details.append(f"Google code {code}")
        codes = [error.get("status")]
        for field in ("errors", "details"):
            entries = error.get(field, [])
            if isinstance(entries, list):
                codes.extend(item.get("reason") for item in entries if isinstance(item, dict))
        safe = sorted({code for code in codes if isinstance(code, str) and code in SAFE_API_CODES})
        if safe:
            details.append("Google reason/status " + ", ".join(safe))
        elif any(code is not None for code in codes):
            details.append("Google reason/status unrecognized (redacted)")
    except (ValueError, TypeError, AttributeError):
        pass
    return "; ".join(details)


def api_execute(request, *, message_id: str | None = None, max_retries=DEFAULT_MAX_RETRIES,
                retry_stats=None, progress=None):
    context = f"; message {safe_message_id(message_id)}" if message_id is not None else ""
    try:
        return execute_with_retry(request, max_retries=max_retries, stats=retry_stats, progress=progress)
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        hint = "Retry later."
        if status == 401:
            hint = "Authorization is no longer usable; run gmail-auth --force."
        elif status == 403:
            hint = "Check API enablement, required scopes, account policy, or quota for the reported reason."
        raise GmailError(f"Gmail API request failed ({_http_error_details(exc)}{context}). {hint}") from exc
    except (ValueError, TypeError) as exc:
        raise GmailError(f"Gmail returned an invalid API response{context}. Retry the request.") from exc
    except RefreshError as exc:
        raise GmailError(f"Gmail authorization expired or was revoked{context}. Run gmail-auth --force.") from exc
    except (TransportError, OSError, httplib2.HttpLib2Error) as exc:
        raise GmailError(f"Gmail network request failed{context}. Check the connection and retry.") from exc


def authorization_status(settings: GmailSettings) -> str:
    if not settings.token_path.exists():
        suffix = "Desktop client file is missing; see docs/GMAIL_SETUP.md." if not settings.credentials_path.exists() else "Run gmail-auth."
        return "Not authorized. " + suffix
    service = gmail_service(settings)
    try:
        api_execute(service.users().getProfile(userId="me"))
        return "Gmail authorization is usable (verified with the read-only API)."
    finally:
        service.close()
