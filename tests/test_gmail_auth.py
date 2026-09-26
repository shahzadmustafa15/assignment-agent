import json
import stat
from unittest.mock import Mock

import pytest
from google.auth.exceptions import RefreshError, TransportError

from app.config import GmailSettings, load_gmail_settings
from app.gmail_auth import (GmailError, SCOPES, api_execute, authenticate, authorization_status,
                            save_token)
from app.main import main


def credentials():
    return Mock(valid=True, refresh_token="synthetic-refresh", scopes=SCOPES, granted_scopes=SCOPES,
                to_json=Mock(return_value=json.dumps({"scopes": list(SCOPES), "token": "synthetic-test-token"})))


def test_missing_credentials_is_actionable(tmp_path):
    settings = GmailSettings(tmp_path / "credentials")
    with pytest.raises(GmailError, match="Google Cloud"):
        authenticate(settings, interactive=True)
    assert not settings.token_path.exists()


def test_no_silent_browser_for_scan(tmp_path):
    with pytest.raises(GmailError, match="not authorized"):
        authenticate(GmailSettings(tmp_path / "credentials"))


def test_atomic_private_token_storage(tmp_path):
    settings = GmailSettings(tmp_path / "credentials")
    save_token(settings, credentials())
    assert stat.S_IMODE(settings.credentials_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(settings.token_path.stat().st_mode) == 0o600
    assert json.loads(settings.token_path.read_text())["scopes"] == list(SCOPES)
    assert list(settings.credentials_dir.iterdir()) == [settings.token_path]


def test_saved_token_reuse_and_refresh(tmp_path, monkeypatch):
    settings = GmailSettings(tmp_path / "credentials")
    value = credentials()
    save_token(settings, value)
    monkeypatch.setattr("app.gmail_auth.Credentials.from_authorized_user_info", Mock(return_value=value))
    assert authenticate(settings) is value
    value.refresh.assert_not_called()
    value.valid = False
    assert authenticate(settings) is value
    value.refresh.assert_called_once()


@pytest.mark.parametrize("exception,match", [(RefreshError("SECRET"), "revoked"), (TransportError("SECRET"), "network")])
def test_refresh_errors_are_sanitized(tmp_path, monkeypatch, exception, match):
    value = credentials()
    value.valid = False
    value.refresh.side_effect = exception
    monkeypatch.setattr("app.gmail_auth._saved_credentials", lambda _: value)
    with pytest.raises(GmailError, match=match) as error:
        authenticate(GmailSettings(tmp_path))
    assert "SECRET" not in str(error.value)


def test_malformed_and_broad_scope_tokens_rejected(tmp_path):
    settings = GmailSettings(tmp_path / "credentials")
    settings.credentials_dir.mkdir()
    settings.token_path.write_text('{"token": "SECRET", "scopes": ["https://mail.google.com/"]}')
    with pytest.raises(GmailError, match="unexpected scopes"):
        authenticate(settings)
    settings.token_path.write_text('broken SECRET')
    with pytest.raises(GmailError, match="malformed") as error:
        authenticate(settings)
    assert "SECRET" not in str(error.value)


def test_oauth_loopback_readonly_flow(tmp_path, monkeypatch):
    settings = GmailSettings(tmp_path / "credentials")
    settings.credentials_dir.mkdir()
    # Synthetic client fixture, never usable as real Google credentials.
    settings.credentials_path.write_text(json.dumps({"installed": {
        "client_id": "synthetic-client", "client_secret": "synthetic-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}))
    flow = Mock()
    value = credentials()
    flow.run_local_server.return_value = value
    factory = Mock(return_value=flow)
    monkeypatch.setattr("app.gmail_auth.InstalledAppFlow.from_client_config", factory)
    assert authenticate(settings, interactive=True) is value
    assert factory.call_args.kwargs["scopes"] == SCOPES
    assert factory.call_args.kwargs["autogenerate_code_verifier"] is True
    assert flow.run_local_server.call_args.kwargs["host"] == "127.0.0.1"
    assert flow.run_local_server.call_args.kwargs["open_browser"] is True
    assert settings.token_path.exists()
    assert stat.S_IMODE(settings.credentials_path.stat().st_mode) == 0o600


def test_symlink_token_refused(tmp_path):
    settings = GmailSettings(tmp_path / "credentials")
    settings.credentials_dir.mkdir()
    target = tmp_path / "unrelated"
    target.write_text("Keep this unchanged")
    settings.token_path.symlink_to(target)
    with pytest.raises(GmailError, match="symbolic"):
        save_token(settings, credentials())
    assert target.read_text() == "Keep this unchanged"


def test_status_no_token_and_live_verification(tmp_path, monkeypatch):
    settings = GmailSettings(tmp_path / "credentials")
    assert "Not authorized" in authorization_status(settings)
    save_token(settings, credentials())
    service = Mock()
    monkeypatch.setattr("app.gmail_auth.gmail_service", Mock(return_value=service))
    assert "usable" in authorization_status(settings)
    service.users.return_value.getProfile.assert_called_once_with(userId="me")
    service.close.assert_called_once()


@pytest.mark.parametrize("code", [401, 403, 429, 503])
def test_api_errors_sanitized(code):
    import httplib2
    from googleapiclient.errors import HttpError
    request = Mock()
    request.execute.side_effect = HttpError(httplib2.Response({"status": str(code)}), b'{"error":"SECRET"}')
    with pytest.raises(GmailError) as error:
        api_execute(request)
    assert "SECRET" not in str(error.value)


def test_gmail_cli_no_auth_does_not_touch_real_account(capsys):
    assert main(["gmail-status"]) == 0
    assert "Not authorized" in capsys.readouterr().out
    assert main(["gmail-auth"]) == 1
    assert "Google Cloud" in capsys.readouterr().err
    assert main(["gmail-scan"]) == 1
    assert "not authorized" in capsys.readouterr().err


def test_config_validation_does_not_break_reminders(monkeypatch, capsys):
    monkeypatch.setenv("GMAIL_SCAN_DAYS", "0")
    assert main(["assignments"]) == 0
    assert main(["gmail-status"]) == 1
    assert "1..365" in capsys.readouterr().err


def test_unknown_api_reason_and_invalid_message_id_are_redacted():
    import httplib2
    from googleapiclient.errors import HttpError
    request = Mock()
    request.execute.side_effect = HttpError(httplib2.Response({"status": "403"}), json.dumps({"error": {
        "code": 403, "errors": [{"reason": "SECRET_TOKEN"}], "message": "SECRET_TOKEN"}}).encode())
    with pytest.raises(GmailError) as error:
        api_execute(request, message_id="bad\nAuthorization: Bearer SECRET_TOKEN")
    text = str(error.value)
    assert "HTTP 403" in text and "Google code 403" in text
    assert "[invalid-id]" in text and "redacted" in text
    assert "SECRET_TOKEN" not in text and "\n" not in text
