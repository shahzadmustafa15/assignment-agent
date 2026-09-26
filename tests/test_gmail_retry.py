import json
from unittest.mock import Mock, call

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.gmail_auth import GmailError, api_execute
from app.gmail_retry import RetryStats, execute_with_retry


def error(status=403, reason="rateLimitExceeded", retry_after=None):
    headers = {"status": str(status)}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return HttpError(httplib2.Response(headers), json.dumps({"error": {
        "code": status, "status": "PERMISSION_DENIED", "errors": [{"reason": reason}],
        "message": "SECRET_MUST_NOT_APPEAR"}}).encode())


@pytest.mark.parametrize("status,reason", [
    (403, "rateLimitExceeded"), (403, "userRateLimitExceeded"), (429, "unspecified"),
    (503, "backendError"), (403, "backendError"), (500, "internalError"),
])
def test_transient_retry(status, reason, monkeypatch):
    request = Mock()
    request.execute.side_effect = [error(status, reason), {"ok": True}]
    sleep = Mock()
    monkeypatch.setattr("app.gmail_retry.time.sleep", sleep)
    monkeypatch.setattr("app.gmail_retry.random.uniform", lambda *_: 0.25)
    stats = RetryStats()
    assert api_execute(request, retry_stats=stats) == {"ok": True}
    assert request.execute.call_args_list == [call(num_retries=0), call(num_retries=0)]
    sleep.assert_called_once_with(1.25)
    assert stats.retries == 1 and stats.rate_limit_failures == 0


def test_exponential_backoff_jitter_and_max_exhaustion(monkeypatch):
    request = Mock()
    request.execute.side_effect = error()
    sleep = Mock()
    monkeypatch.setattr("app.gmail_retry.time.sleep", sleep)
    monkeypatch.setattr("app.gmail_retry.random.uniform", lambda *_: 0.4)
    stats = RetryStats()
    progress = Mock()
    with pytest.raises(GmailError, match="rateLimitExceeded") as caught:
        api_execute(request, retry_stats=stats, max_retries=4, progress=progress)
    assert sleep.call_args_list == [call(1.4), call(2.4), call(4.4), call(8.4)]
    assert request.execute.call_count == 5
    assert stats.retries == 4 and stats.rate_limit_failures == 1
    assert "Rate limit hit. Retrying in 2.4 seconds" in progress.call_args_list[1].args[0]
    assert "SECRET_MUST_NOT_APPEAR" not in str(caught.value)


@pytest.mark.parametrize("header,expected", [("10", 10.0), ("0", 1.0), ("not-a-date", 1.0),
    ("Thu, 01 Jan 1970 00:00:20 GMT", 10.0)])
def test_retry_after_numeric_and_http_date(monkeypatch, header, expected):
    monkeypatch.setattr("app.gmail_retry.time.time", lambda: 10.0)
    monkeypatch.setattr("app.gmail_retry.random.uniform", lambda *_: 0.0)
    sleep = Mock()
    monkeypatch.setattr("app.gmail_retry.time.sleep", sleep)
    request = Mock()
    request.execute.side_effect = [error(retry_after=header), {}]
    assert execute_with_retry(request) == {}
    sleep.assert_called_once_with(expected)


def test_long_retry_after_defers_instead_of_retrying_too_early(monkeypatch):
    sleep = Mock()
    progress = Mock()
    monkeypatch.setattr("app.gmail_retry.time.sleep", sleep)
    request = Mock()
    request.execute.side_effect = error(retry_after="120")
    stats = RetryStats()
    with pytest.raises(HttpError):
        execute_with_retry(request, stats=stats, progress=progress)
    assert request.execute.call_count == 1 and stats.rate_limit_failures == 1
    sleep.assert_not_called()
    assert "deferring" in progress.call_args.args[0]


@pytest.mark.parametrize("status,reason", [(403, "domainPolicy"), (403, "insufficientPermissions"), (401, "authError"), (404, "notFound")])
def test_permanent_errors_not_retried(monkeypatch, status, reason):
    sleep = Mock()
    monkeypatch.setattr("app.gmail_retry.time.sleep", sleep)
    request = Mock()
    request.execute.side_effect = error(status, reason)
    stats = RetryStats()
    with pytest.raises(GmailError):
        api_execute(request, retry_stats=stats)
    assert request.execute.call_count == 1 and stats.retries == stats.rate_limit_failures == 0
    sleep.assert_not_called()


def test_retries_disabled(monkeypatch):
    request = Mock()
    request.execute.side_effect = error()
    sleep = Mock()
    monkeypatch.setattr("app.gmail_retry.time.sleep", sleep)
    with pytest.raises(GmailError):
        api_execute(request, max_retries=0)
    assert request.execute.call_count == 1
    sleep.assert_not_called()
