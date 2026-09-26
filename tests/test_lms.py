"""Stage 1 tests use synthetic pages, private temporary files, and mocked browsers."""
import json
from unittest.mock import MagicMock, Mock
import pytest
from app.lms.base import LMSSettings, LMSError, validate_url
from app.lms.bahria import BahriaLMSAdapter
from app.lms.session import read_state, save_state, session_status
from app.lms.parser import inspect_structure, safe_text, safe_url


@pytest.fixture
def browser_setup(tmp_path):
    settings = LMSSettings("https://portal.example.edu/", tmp_path / "credentials/bahria_storage_state.json")
    factory = MagicMock()
    browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.url = settings.portal_url + "dashboard"
    page.frames = []
    page.is_closed.return_value = False
    context.pages = [page]
    page.goto.return_value.status = 200
    context.storage_state.return_value = {"cookies": [], "origins": []}
    return settings, factory, browser, context, page


def test_missing_state(browser_setup, capsys):
    settings, factory, *_ = browser_setup
    assert BahriaLMSAdapter(settings, playwright_factory=factory).run("lms-status") == 1
    factory.assert_not_called()
    assert "absent" in capsys.readouterr().out


def test_auth_private_state(browser_setup):
    settings, factory, browser, context, page = browser_setup
    assert BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: "yes").run("lms-auth") == 0
    assert read_state(settings.state_path) == {"cookies": [], "origins": []}
    assert settings.state_path.stat().st_mode & 0o777 == 0o600
    options = factory.return_value.__enter__.return_value.chromium.launch.call_args.kwargs
    assert options["headless"] is False and options["chromium_sandbox"] is True
    context.storage_state.assert_called_once_with(indexed_db=True)
    browser.close.assert_called_once()


def test_auth_cancel_preserves_state(browser_setup):
    settings, factory, *_ = browser_setup
    save_state(settings.state_path, {"cookies": [], "origins": [], "old": True})
    before = settings.state_path.read_bytes()
    assert BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: "no").run("lms-auth") == 1
    assert settings.state_path.read_bytes() == before


@pytest.mark.parametrize("url,expected", [("https://portal.example.edu/dashboard", "session appears usable"),
    ("https://portal.example.edu/Login.aspx", "reauthentication required"),
    ("https://identity.example.edu/", "authentication unverified")])
def test_session_states(browser_setup, url, expected):
    settings, _, _, _, page = browser_setup
    page.url = url
    assert session_status(page, settings.portal_url).startswith(expected)


def test_password_form_detected(browser_setup):
    settings, _, _, _, page = browser_setup
    frame = MagicMock()
    frame.locator.return_value.count.return_value = 1
    frame.locator.return_value.nth.return_value.is_visible.return_value = True
    page.frames = [frame]
    assert "reauthentication required" in session_status(page, settings.portal_url)


def test_status_reuses_state(browser_setup, capsys):
    settings, factory, _, context, _ = browser_setup
    state = {"cookies": [], "origins": []}
    save_state(settings.state_path, state)
    assert BahriaLMSAdapter(settings, playwright_factory=factory).run("lms-status") == 0
    context.storage_state.assert_not_called()
    assert "not verified" in capsys.readouterr().out


def test_expired_inspection_does_not_dump(browser_setup):
    settings, factory, _, _, page = browser_setup
    save_state(settings.state_path, {"cookies": [], "origins": []})
    page.url = settings.portal_url + "login"
    with pytest.raises(LMSError, match="Reauthentication required"):
        BahriaLMSAdapter(settings, playwright_factory=factory).run("lms-inspect")
    page.title.assert_not_called()


def test_inspection_no_state_mutation(browser_setup, monkeypatch, capsys):
    settings, factory, _, context, page = browser_setup
    save_state(settings.state_path, {"cookies": [], "origins": []})
    before = settings.state_path.read_bytes()
    page.title.return_value = "Assignments"
    page.locator.return_value.count.return_value = 0
    def blocked(*a, **k):
        pytest.fail("Stage 1 must not access database or notifications")
    monkeypatch.setattr("app.database.Database.__init__", blocked)
    monkeypatch.setattr("app.notifier.DesktopNotifier.send", blocked)
    assert BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: "").run("lms-inspect") == 0
    assert settings.state_path.read_bytes() == before
    context.storage_state.assert_not_called()
    assert "table_headings" in capsys.readouterr().out


def test_structural_redaction(browser_setup):
    *_, page = browser_setup
    page.url = "https://portal.example.edu/secret?token=SECRET#SECRET"
    page.title.return_value = "Welcome Student Name"
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.nth.return_value.is_visible.return_value = True
    page.locator.return_value.nth.return_value.inner_text.return_value = "teacher@example.edu 123456789"
    output = json.dumps(inspect_structure(page))
    assert "SECRET" not in output and "teacher@example.edu" not in output and "123456789" not in output
    assert "Student Name" not in output
    page.evaluate.assert_not_called()


def test_bad_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json")
    path.chmod(0o600)
    with pytest.raises(LMSError, match="malformed"):
        read_state(path)


def test_public_state_rejected(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"cookies": [], "origins": []})
    path.chmod(0o644)
    with pytest.raises(LMSError, match="private"):
        read_state(path)


@pytest.mark.parametrize("url", ["", "http://portal.example.edu", "https://user:password@portal.example.edu", "https://portal.example.edu/#token"])
def test_url_validation(url):
    with pytest.raises(LMSError):
        validate_url(url)


def test_navigation_errors_sanitized(browser_setup):
    from playwright.sync_api import Error
    settings, factory, browser, _, page = browser_setup
    page.goto.side_effect = Error("URL contains SECRET")
    with pytest.raises(LMSError) as error:
        BahriaLMSAdapter(settings, playwright_factory=factory).run("lms-auth")
    assert "SECRET" not in str(error.value)
    browser.close.assert_called_once()


@pytest.mark.parametrize("command", ["lms-auth", "lms-status", "lms-inspect"])
def test_cli_dispatch_avoids_database(command, monkeypatch, browser_setup):
    from app.main import main
    settings, *_ = browser_setup
    monkeypatch.setattr("app.lms.base.load_settings", lambda: settings)
    run = Mock(return_value=0)
    monkeypatch.setattr(BahriaLMSAdapter, "run", run)
    def blocked(*a, **k):
        pytest.fail("LMS command opened assignment database")
    monkeypatch.setattr("app.main.Database", blocked)
    assert main([command]) == 0
    run.assert_called_once_with(command)


@pytest.fixture(autouse=True)
def prevent_real_playwright(monkeypatch):
    def blocked():
        pytest.fail("Tests must inject a mocked Playwright factory")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", blocked)


def test_auth_rejects_login_page(browser_setup):
    settings, factory, _, _, page = browser_setup
    page.url = settings.portal_url + "login"
    with pytest.raises(LMSError, match="could not be confirmed"):
        BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: "yes").run("lms-auth")
    assert not settings.state_path.exists()


def test_auth_prompts_for_url_only_when_unconfigured(browser_setup):
    settings, factory, _, _, _ = browser_setup
    settings = LMSSettings("", settings.state_path)
    answers = iter(["https://portal.example.edu/", "yes"])
    assert BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: next(answers)).run("lms-auth") == 0
    assert json.loads(settings.state_path.with_name("bahria_portal.json").read_text())["portal_url"] == "https://portal.example.edu/"


def test_browser_options_keep_sandbox_enabled(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from app.lms.session import browser_options
    monkeypatch.setattr(Path, "stat", lambda _: SimpleNamespace(st_mode=0o104755, st_uid=0))
    options = browser_options(False)
    assert options["chromium_sandbox"] is True
    assert options["env"]["CHROME_DEVEL_SANDBOX"] == "/opt/google/chrome/chrome-sandbox"
    monkeypatch.setattr(Path, "stat", lambda _: SimpleNamespace(st_mode=0o104777, st_uid=1000))
    assert "env" not in browser_options(False)


@pytest.mark.parametrize("answer", ["", "0", "3", "wrong"])
def test_invalid_tab_selection(browser_setup, answer):
    settings, factory, _, context, page = browser_setup
    context.pages = [page, page]
    page.title.return_value = "Dashboard"
    with pytest.raises(LMSError, match="No valid tab"):
        BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: answer).select_inspection_page(context, settings.portal_url)


def test_new_lms_tab_selected_with_explicit_origin_confirmation(browser_setup, capsys):
    settings, factory, _, context, page = browser_setup
    page.title.return_value = "Dashboard"
    popup = MagicMock()
    popup.is_closed.return_value = False
    popup.url = "https://lms.example.edu/assignments?token=SECRET"
    popup.title.return_value = "Assignments"
    popup.frames = []
    context.pages = [page, popup]
    answers = iter(["2", "yes"])
    selected = BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: next(answers)).select_inspection_page(context, settings.portal_url)
    assert selected is popup
    assert "SECRET" not in capsys.readouterr().out
    context.storage_state.assert_not_called()


def test_declined_external_tab_not_inspected(browser_setup):
    settings, factory, _, context, page = browser_setup
    page.url = "https://lms.example.edu/assignments"
    with pytest.raises(LMSError, match="Different-origin inspection cancelled"):
        BahriaLMSAdapter(settings, playwright_factory=factory, prompt=lambda _: "no").select_inspection_page(context, settings.portal_url)
    page.locator.assert_not_called()


def test_closed_tabs_ignored(browser_setup):
    settings, factory, _, context, page = browser_setup
    closed = MagicMock()
    closed.is_closed.return_value = True
    context.pages = [closed, page]
    selected = BahriaLMSAdapter(settings, playwright_factory=factory).select_inspection_page(context, settings.portal_url)
    assert selected is page


def test_selected_login_tab_rejected(browser_setup):
    settings, factory, _, context, page = browser_setup
    page.url = settings.portal_url + "login"
    with pytest.raises(LMSError, match="requires login"):
        BahriaLMSAdapter(settings, playwright_factory=factory).select_inspection_page(context, settings.portal_url)
