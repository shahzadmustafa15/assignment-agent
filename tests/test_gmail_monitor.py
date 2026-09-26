from dataclasses import replace
from unittest.mock import MagicMock, Mock

from app.config import GmailSettings
from app.gmail_monitor import scan_gmail
from app.notifier import Notifier, NotificationError


def api_for(*messages):
    api = MagicMock()
    resource = api.users.return_value.messages.return_value
    resource.list.return_value.execute.return_value = {"messages": [{"id": message["id"]} for message in messages]}
    by_id = {message["id"]: message for message in messages}
    resource.get.side_effect = lambda **kwargs: Mock(execute=Mock(return_value=by_id[kwargs["id"]]))
    return api, resource


def test_scan_creation_and_dedup(database, tmp_path, gmail_message):
    api, resource = api_for(gmail_message())
    notifier = Mock(spec=Notifier)
    settings = GmailSettings(tmp_path)
    result = scan_gmail(api, database, settings, notifier)
    assert result.created == result.notified == 1
    assert not result.errors
    assert len(database.list_assignments()) == 1
    notifier.send.assert_called_once()
    assert notifier.send.call_args.args[0] == "New Assignment Detected"
    assert "Artificial Intelligence" in notifier.send.call_args.args[1]
    assert "11:59 PM" in notifier.send.call_args.args[1]
    result = scan_gmail(api, database, settings, notifier)
    assert result.created == result.notified == 0 and result.skipped == 1
    assert resource.get.call_count == 2  # Metadata then full; second scan fetches neither.
    assert [call.kwargs["format"] for call in resource.get.call_args_list] == ["metadata", "full"]
    notifier.send.assert_called_once()
    from app.gmail_monitor import candidate_query
    resource.list.assert_any_call(userId="me", q=candidate_query(settings),
                                     maxResults=100, includeSpamTrash=False, fields="messages/id,nextPageToken")


def test_duplicate_natural_key_is_processed_without_notification(database, tmp_path, gmail_message):
    first = gmail_message()
    second = gmail_message(message_id="different-id")
    api, _ = api_for(first, second)
    notifier = Mock(spec=Notifier)
    result = scan_gmail(api, database, GmailSettings(tmp_path), notifier)
    assert result.created == 1 and result.skipped == 1
    assert database.gmail_message_processed("different-id")
    notifier.send.assert_called_once()


def test_sender_filter_and_ignored_dedup(database, tmp_path, gmail_message):
    api, resource = api_for(gmail_message(sender="someone@other.invalid"))
    notifier = Mock(spec=Notifier)
    settings = GmailSettings(tmp_path, university_domain="example.edu")
    assert scan_gmail(api, database, settings, notifier).ignored == 1
    assert scan_gmail(api, database, settings, notifier).skipped == 1
    assert database.list_assignments() == []
    resource.get.assert_called_once()
    notifier.send.assert_not_called()


def test_malformed_message_retry_and_valid_message_continues(database, tmp_path, gmail_message):
    bad = gmail_message(message_id="bad")
    bad["payload"]["body"]["data"] = "!!!"
    api, _ = api_for(bad, gmail_message())
    result = scan_gmail(api, database, GmailSettings(tmp_path), Mock(spec=Notifier))
    assert result.created == 1 and len(result.errors) == 1
    assert not database.gmail_message_processed("bad")


def test_missing_deadline_is_saved_for_review(database, tmp_path, gmail_message):
    api, _ = api_for(gmail_message(subject="Assignment posted", body=""))
    result = scan_gmail(api, database, GmailSettings(tmp_path), Mock(spec=Notifier))
    record = database.list_assignments()[0]
    assert result.created == 1
    assert record.deadline is None and record.needs_review


def test_notification_failure_retries_without_reimport(database, tmp_path, gmail_message):
    api, _ = api_for(gmail_message())
    notifier = Mock(spec=Notifier)
    notifier.send.side_effect = NotificationError("Synthetic desktop failure")
    result = scan_gmail(api, database, GmailSettings(tmp_path), notifier)
    assert result.created == 1 and result.notified == 0 and len(result.errors) == 1
    notifier.send.side_effect = None
    result = scan_gmail(api, database, GmailSettings(tmp_path), notifier)
    assert result.created == 0 and result.notified == 1
    assert scan_gmail(api, database, GmailSettings(tmp_path), notifier).notified == 0
    assert notifier.send.call_count == 2


def test_pagination_and_cap(database, tmp_path, gmail_message):
    first = gmail_message()
    second = gmail_message(message_id="second", subject="A different assignment", body="New homework")
    api, resource = api_for(first, second)
    resource.list.return_value.execute.side_effect = [
        {"messages": [{"id": first["id"]}], "nextPageToken": "page2"},
        {"messages": [{"id": second["id"]}]},
        {"messages": []},  # Broader query follows the primary query pages.
    ]
    settings = GmailSettings(tmp_path, scan_days=3)
    assert scan_gmail(api, database, settings, Mock(spec=Notifier)).created == 2
    assert any(call.kwargs.get("pageToken") == "page2" for call in resource.list.call_args_list)
    api, resource = api_for(first)
    resource.list.return_value.execute.return_value["nextPageToken"] = "page2"
    assert scan_gmail(api, database, replace(settings, max_messages=1), Mock(spec=Notifier)).truncated


def test_api_failure_does_not_mark_message_processed(database, tmp_path, gmail_message):
    import httplib2
    from googleapiclient.errors import HttpError
    api, resource = api_for(gmail_message())
    resource.get.side_effect = None
    resource.get.return_value.execute.side_effect = HttpError(httplib2.Response({"status": "503"}), b'{"error":"SECRET"}')
    result = scan_gmail(api, database, GmailSettings(tmp_path), Mock(spec=Notifier))
    assert len(result.errors) == 1 and "SECRET" not in str(result.errors)
    assert not database.gmail_message_processed("synthetic-message-1")


def test_malformed_api_payload(database, tmp_path, gmail_message):
    api, resource = api_for(gmail_message())
    resource.get.side_effect = None
    resource.get.return_value.execute.return_value = None
    result = scan_gmail(api, database, GmailSettings(tmp_path), Mock(spec=Notifier))
    assert result.created == 0 and len(result.errors) == 1


def test_concurrent_import_and_notification_only_once(database, gmail_message):
    from concurrent.futures import ThreadPoolExecutor
    from app.assignment_parser import assignment_from_email, parse_email
    email = parse_email(gmail_message())
    record = assignment_from_email(email)
    notifier = Mock()
    def import_message(_):
        outcome = database.record_gmail_message(email.message_id, email.subject, email.sender, email.received_at, record)
        database.notify_gmail_once(email.message_id, notifier)
        return outcome
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(import_message, range(2))) == ["created", "duplicate"]
    assert len(database.list_assignments()) == 1
    notifier.assert_called_once()


def test_dry_run_never_touches_database_or_notifier(tmp_path, gmail_message):
    from app.database import Database
    api, _ = api_for(gmail_message(), gmail_message(message_id="fees", subject="Fee Payment Due Date Passed", body=""))
    database = Mock(spec=Database)
    notifier = Mock(spec=Notifier)
    result = scan_gmail(api, database, GmailSettings(tmp_path), notifier, dry_run=True, verbose=True)
    assert result.accepted == 1 and result.ignored == 1
    assert result.created == result.notified == 0
    assert len(result.classifications) == 2
    assert database.mock_calls == []
    notifier.send.assert_not_called()


def test_dry_run_reclassifies_processed_mail_and_preserves_pending_notifications(database, tmp_path, gmail_message):
    api, _ = api_for(gmail_message())
    notifier = Mock(spec=Notifier)
    notifier.send.side_effect = NotificationError("Synthetic failure")
    scan_gmail(api, database, GmailSettings(tmp_path), notifier)
    before = database.path.read_bytes()
    notifier.reset_mock()
    result = scan_gmail(api, database, GmailSettings(tmp_path), notifier, dry_run=True)
    assert result.accepted == 1 and result.skipped == 0
    assert database.path.read_bytes() == before
    notifier.send.assert_not_called()
    assert database.pending_gmail_notifications() == ["synthetic-message-1"]


def test_cli_dry_run_avoids_even_database_initialization(tmp_path, gmail_message, monkeypatch, capsys):
    from app.main import main
    api, _ = api_for(gmail_message(subject="Assignment 3 due tomorrow", body="PRIVATE_BODY_DO_NOT_PRINT"))
    monkeypatch.setattr("app.gmail_auth.gmail_service", Mock(return_value=api))
    db = Mock(side_effect=AssertionError("Dry-run must not construct Database"))
    notify = Mock(side_effect=AssertionError("Dry-run must not notify"))
    monkeypatch.setattr("app.main.Database", db)
    monkeypatch.setattr("app.main.DesktopNotifier.send", notify)
    assert main(["gmail-scan", "--dry-run", "--verbose"]) == 0
    output = capsys.readouterr().out
    for field in ("Subject:", "Sender:", "Classification: ASSIGNMENT", "Score:", "Reasons:"):
        assert field in output
    assert "PRIVATE_BODY_DO_NOT_PRINT" not in output
    assert not (tmp_path / "data").exists()
    db.assert_not_called()
    notify.assert_not_called()


def test_error_reason_and_id_reported_while_remaining_mail_continues(database, tmp_path, gmail_message):
    import json
    import httplib2
    from googleapiclient.errors import HttpError
    bad = gmail_message(message_id="123abc")
    good = gmail_message(message_id="456def")
    api, resource = api_for(bad, good)
    error = HttpError(httplib2.Response({"status": "403"}), json.dumps({"error": {
        "code": 403, "status": "PERMISSION_DENIED", "message": "SECRET_TOKEN_DO_NOT_PRINT",
        "errors": [{"reason": "domainPolicy", "message": "SECRET_TOKEN_DO_NOT_PRINT"}]}}).encode())
    def get(**kwargs):
        return Mock(execute=Mock(side_effect=error)) if kwargs["id"] == "123abc" else Mock(execute=Mock(return_value=good))
    resource.get.side_effect = get
    result = scan_gmail(api, database, GmailSettings(tmp_path), Mock(spec=Notifier))
    assert result.created == 1 and len(result.errors) == 1
    text = result.errors[0]
    assert "HTTP 403" in text and "domainPolicy" in text and "123abc" in text and "Google code 403" in text
    assert "SECRET_TOKEN" not in text
    assert not database.gmail_message_processed("123abc")


def test_metadata_filter_reduces_full_fetches(tmp_path, gmail_message):
    messages = [gmail_message(message_id="fee", subject="Fee Payment Due Date Passed", body=""),
                gmail_message(message_id="review", subject="Real reviews. Real results. Meet My Notes.", body=""),
                gmail_message(message_id="assignment", subject="Assignment 3 due tomorrow", body="")]
    api, resource = api_for(*messages)
    notifier = Mock(spec=Notifier)
    result = scan_gmail(api, None, GmailSettings(tmp_path), notifier, dry_run=True)
    full_calls = [call for call in resource.get.call_args_list if call.kwargs["format"] == "full"]
    assert len(full_calls) == 1 and full_calls[0].kwargs["id"] == "assignment"
    assert result.candidate_ids == result.metadata_fetched == 3
    assert result.full_fetched == result.accepted == 1 and result.ignored == 2
    assert result.rate_limit_failures == result.other_errors == 0
    notifier.send.assert_not_called()


def test_snippet_can_qualify_vague_subject(tmp_path, gmail_message):
    message = gmail_message(subject="Notification", body="Course: AI\nAssignment: Assignment 2")
    message["snippet"] = "Your instructor has posted a new assignment for your course."
    api, resource = api_for(message)
    result = scan_gmail(api, None, GmailSettings(tmp_path), Mock(spec=Notifier), dry_run=True)
    assert result.full_fetched == 1 and result.accepted == 1


def test_candidate_query_and_safe_defaults(tmp_path, monkeypatch):
    import pytest
    from app.config import load_gmail_settings
    from app.gmail_monitor import candidate_query
    settings = load_gmail_settings()
    assert settings.scan_days == 7 and settings.max_messages == 100
    assert settings.fetch_delay_ms == 100 and settings.max_retries == 4
    query = candidate_query(settings)
    assert 'newer_than:7d' in query and '{"assignment"' in query and '"homework"' in query
    assert '"deadline"' not in query and '"project"' not in query
    assert 'is:unread' not in query
    assert candidate_query(replace(settings, unread_only=True)).endswith('is:unread')
    with pytest.raises(ValueError, match="plain words"):
        candidate_query(replace(settings, search_terms=('assignment" OR in:anywhere',)))
    monkeypatch.setenv("GMAIL_FETCH_DELAY_MS", "-1")
    with pytest.raises(ValueError, match="GMAIL_FETCH_DELAY_MS"):
        load_gmail_settings()


def test_full_message_fetch_pacing(tmp_path, gmail_message, monkeypatch):
    from unittest.mock import call
    api, _ = api_for(gmail_message(), gmail_message(message_id="second"))
    sleep = Mock()
    monkeypatch.setattr("app.gmail_monitor.time.sleep", sleep)
    result = scan_gmail(api, None, GmailSettings(tmp_path, fetch_delay_ms=125), Mock(spec=Notifier), dry_run=True)
    assert result.full_fetched == 2
    assert sleep.call_args_list == [call(0.125)]


def test_scan_continues_after_rate_exhaustion_and_reports_counts(tmp_path, gmail_message, capsys):
    import json
    import httplib2
    from googleapiclient.errors import HttpError
    bad = gmail_message(message_id="abc123")
    good = gmail_message(message_id="def456")
    api, resource = api_for(bad, good)
    quota_error = HttpError(httplib2.Response({"status": "403"}), json.dumps({"error": {
        "errors": [{"reason": "rateLimitExceeded"}], "status": "PERMISSION_DENIED"}}).encode())
    bad_request = Mock(execute=Mock(side_effect=quota_error))
    def get(**kwargs):
        return bad_request if kwargs["id"] == "abc123" else Mock(execute=Mock(return_value=good))
    resource.get.side_effect = get
    notifier = Mock(spec=Notifier)
    result = scan_gmail(api, None, GmailSettings(tmp_path, max_retries=2), notifier, dry_run=True, verbose=True)
    assert result.candidate_ids == 2 and result.full_fetched == result.accepted == 1
    assert result.retries == 2 and result.rate_limit_failures == 1 and result.other_errors == 0
    assert len(result.errors) == 1 and bad_request.execute.call_count == 3
    assert "Rate limit hit. Retrying in" in capsys.readouterr().out
    notifier.send.assert_not_called()


def test_list_rate_failure_still_returns_summary(tmp_path, gmail_message):
    import httplib2
    from googleapiclient.errors import HttpError
    api, resource = api_for(gmail_message())
    resource.list.return_value.execute.side_effect = HttpError(httplib2.Response({"status": "429"}), b'{}')
    result = scan_gmail(api, None, GmailSettings(tmp_path, max_retries=1), Mock(spec=Notifier), dry_run=True)
    assert result.retries == 1 and result.rate_limit_failures == 1 and result.candidate_ids == 0
    assert len(result.errors) == 1
