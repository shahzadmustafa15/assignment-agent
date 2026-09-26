from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest

from app.config import GmailSettings, load_gmail_settings
from app.gmail_discovery import discovery_queries, recent_query
from app.gmail_monitor import scan_gmail
from app.notifier import Notifier


def service_for(settings, message_map, query_ids):
    service = MagicMock()
    resource = service.users.return_value.messages.return_value
    resource.list.side_effect = lambda **kwargs: Mock(execute=Mock(return_value={
        "messages": [{"id": value} for value in query_ids.get(kwargs["q"], [])][:kwargs["maxResults"]]}))
    resource.get.side_effect = lambda **kwargs: Mock(execute=Mock(return_value=message_map[kwargs["id"]]))
    return service, resource


def test_multiple_queries_merge_ids_and_fetch_once(tmp_path, gmail_message):
    settings = GmailSettings(tmp_path)
    primary, broad = discovery_queries(settings)
    emails = {key: gmail_message(message_id=key) for key in ("a", "b", "c")}
    service, resource = service_for(settings, emails, {primary: ["a", "b"], broad: ["b", "c"]})
    notifier = Mock(spec=Notifier)
    database = Mock()
    result = scan_gmail(service, database, settings, notifier, dry_run=True)
    assert result.candidate_ids == result.examined == result.metadata_candidates == 3
    assert result.discovery_duplicates == 1
    assert [item.ids_returned for item in result.discovery_reports] == [2, 2]
    assert [item.unique_added for item in result.discovery_reports] == [2, 1]
    assert resource.get.call_count == 6  # Each unique ID: metadata once, full once.
    assert database.mock_calls == []
    notifier.send.assert_not_called()
    assert not result.fallback_used


def test_broad_query_finds_weak_candidates_without_weakening_classifier(tmp_path, gmail_message):
    settings = GmailSettings(tmp_path)
    _, broad = discovery_queries(settings)
    emails = {"a": gmail_message(message_id="a", subject="Course deadline tomorrow", body="Class schedule update")}
    service, resource = service_for(settings, emails, {broad: ["a"]})
    result = scan_gmail(service, None, settings, Mock(spec=Notifier), dry_run=True)
    assert result.candidate_ids == result.metadata_candidates == result.full_fetched == 1
    assert result.accepted == 0 and result.ignored == 1
    decision = result.classifications[0].result
    assert settings.score_threshold == 6 and decision.threshold == 8
    assert decision.classification == "REJECTED"
    assert "no assignment-specific phrase (required)" in decision.reasons
    assert not result.fallback_used


def test_zero_results_use_metadata_first_fallback(tmp_path, gmail_message):
    settings = GmailSettings(tmp_path)
    emails = {
        "academic": gmail_message(message_id="academic", subject="Course update", body="Assignment 4 posted for your class"),
        "fees": gmail_message(message_id="fees", subject="Fee Payment Due Date Passed", body=""),
        "unrelated": gmail_message(message_id="unrelated", subject="Dinner plans", body="See you soon"),
    }
    service, resource = service_for(settings, emails, {recent_query(settings): list(emails)})
    notifier = Mock(spec=Notifier)
    result = scan_gmail(service, None, settings, notifier, dry_run=True, verbose=True)
    assert result.fallback_used and len(result.discovery_reports) == 3
    assert result.discovery_reports[-1].fallback
    assert resource.list.call_args.kwargs["maxResults"] == 25
    assert result.metadata_fetched == 3 and result.metadata_candidates == result.full_fetched == result.accepted == 1
    assert result.ignored == 2
    full_ids = [call.kwargs["id"] for call in resource.get.call_args_list if call.kwargs["format"] == "full"]
    assert full_ids == ["academic"]
    notifier.send.assert_not_called()


def test_fallback_disabled_or_bounded(tmp_path, gmail_message):
    settings = GmailSettings(tmp_path, fallback_messages=0)
    service, resource = service_for(settings, {}, {})
    assert not scan_gmail(service, None, settings, Mock(spec=Notifier), dry_run=True).fallback_used
    assert resource.list.call_count == 2
    settings = replace(settings, fallback_messages=25, max_messages=2)
    emails = {str(i): gmail_message(message_id=str(i)) for i in range(10)}
    service, resource = service_for(settings, emails, {recent_query(settings): list(emails)})
    result = scan_gmail(service, None, settings, Mock(spec=Notifier), dry_run=True)
    assert result.candidate_ids == 2 and resource.list.call_args.kwargs["maxResults"] == 2


def test_global_limit_is_unique_ids_not_sum_of_query_limits(tmp_path, gmail_message):
    settings = GmailSettings(tmp_path, max_messages=3)
    primary, broad = discovery_queries(settings)
    emails = {str(i): gmail_message(message_id=str(i)) for i in range(4)}
    service, resource = service_for(settings, emails, {primary: ["0", "1"], broad: ["2", "3"]})
    result = scan_gmail(service, None, settings, Mock(spec=Notifier), dry_run=True)
    assert result.candidate_ids == 3
    assert resource.list.call_args.kwargs["maxResults"] == 1
    assert all(call.kwargs["id"] != "3" for call in resource.get.call_args_list)


def test_cli_discovery_debug_has_counts_no_body_or_database(tmp_path, gmail_message, monkeypatch, capsys):
    from app.main import main
    settings = load_gmail_settings()
    primary, broad = discovery_queries(settings)
    email = gmail_message(subject="Assignment 4 posted", body="PRIVATE_BODY_SENTINEL")
    service, _ = service_for(settings, {email["id"]: email}, {primary: [email["id"]], broad: [email["id"]]})
    monkeypatch.setattr("app.gmail_auth.gmail_service", Mock(return_value=service))
    database = Mock(side_effect=AssertionError("Dry-run must not open SQLite"))
    notify = Mock(side_effect=AssertionError("Dry-run must not notify"))
    monkeypatch.setattr("app.main.Database", database)
    monkeypatch.setattr("app.main.DesktopNotifier.send", notify)
    assert main(["gmail-scan", "--dry-run", "--verbose", "--discovery-debug"]) == 0
    output = capsys.readouterr().out
    assert output.count("Gmail query:") == 2
    assert "IDs returned: 1" in output and "Unique IDs after deduplication: 1" in output
    assert "duplicate IDs merged: 1" in output and "Fallback scan used: no" in output
    assert "Metadata candidate count: 1" in output and "PRIVATE_BODY_SENTINEL" not in output
    assert not (tmp_path / "data").exists()
    database.assert_not_called()
    notify.assert_not_called()


def test_config_defaults_and_validation(monkeypatch):
    settings = load_gmail_settings()
    assert settings.scan_days == 7 and settings.max_messages == 100 and settings.fallback_messages == 25
    assert set(settings.discovery_terms) == {"deadline", "due", "submission", "LMS", "course", "class"}
    monkeypatch.setenv("GMAIL_FALLBACK_MESSAGES", "51")
    with pytest.raises(ValueError, match="GMAIL_FALLBACK_MESSAGES"):
        load_gmail_settings()
