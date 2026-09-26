"""Bounded Gmail retry policy, independent of OAuth and message processing."""

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import random
import time

from googleapiclient.errors import HttpError

DEFAULT_MAX_RETRIES = 4
BASE_DELAY_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
JITTER_SECONDS = 0.5
MAX_SERVER_WAIT_SECONDS = 60.0
RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "RESOURCE_EXHAUSTED"})
TRANSIENT_REASONS = RATE_REASONS | {"backendError"}
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass
class RetryStats:
    retries: int = 0
    rate_limit_failures: int = 0


def error_reasons(error: HttpError) -> set[str]:
    try:
        payload = json.loads(error.content).get("error", {})
        if not isinstance(payload, dict):
            return set()
        values = [payload.get("status")]
        for key in ("errors", "details"):
            entries = payload.get(key, [])
            if isinstance(entries, list):
                values.extend(item.get("reason") for item in entries if isinstance(item, dict))
        return {value for value in values if isinstance(value, str)}
    except (ValueError, TypeError, AttributeError):
        return set()


def retry_after_seconds(response) -> float:
    value = next((value for key, value in response.items() if str(key).lower() == "retry-after"), None)
    if value is None:
        return 0.0
    try:
        if str(value).strip().isdigit():
            return float(int(str(value).strip()))
        date = parsedate_to_datetime(str(value))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, date.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0.0


def execute_with_retry(request, *, max_retries=DEFAULT_MAX_RETRIES, stats=None, progress=None):
    if not 0 <= max_retries <= 6:
        raise ValueError("GMAIL_MAX_RETRIES must be 0..6")
    stats = stats if stats is not None else RetryStats()
    for attempt in range(max_retries + 1):
        try:
            # A single retry layer: no additional SDK exponential retry loop.
            return request.execute(num_retries=0)
        except HttpError as error:
            status = getattr(error.resp, "status", None)
            reasons = error_reasons(error)
            rate_limited = status == 429 or bool(reasons & RATE_REASONS)
            retryable = status in TRANSIENT_STATUSES or bool(reasons & TRANSIENT_REASONS)
            server_wait = retry_after_seconds(error.resp)
            if not retryable or attempt == max_retries or server_wait > MAX_SERVER_WAIT_SECONDS:
                stats.rate_limit_failures += int(rate_limited)
                if retryable and server_wait > MAX_SERVER_WAIT_SECONDS and progress:
                    progress("Server requested a wait longer than 60 seconds; deferring this request to a later scan.")
                raise
            delay = max(server_wait, min(BASE_DELAY_SECONDS * 2 ** attempt, BACKOFF_CAP_SECONDS)
                        + random.uniform(0.0, JITTER_SECONDS))
            if progress:
                label = "Rate limit hit" if rate_limited else "Temporary Gmail error"
                progress(f"{label}. Retrying in {delay:.1f} seconds (retry {attempt + 1}/{max_retries})...")
            time.sleep(delay)
            stats.retries += 1
