"""Bounded, broad Gmail ID discovery, independent of assignment classification."""

from dataclasses import dataclass, field
import re

from app.config import GmailSettings
from app.gmail_auth import GmailError

PAGE_SIZE = 100


@dataclass
class QueryReport:
    query: str
    fallback: bool = False
    ids_returned: int = 0
    unique_added: int = 0
    pages: int = 0
    failed: bool = False


@dataclass
class DiscoveryResult:
    ids: list[str] = field(default_factory=list)
    queries: list[QueryReport] = field(default_factory=list)
    duplicates: int = 0
    fallback_used: bool = False
    truncated: bool = False
    errors: list[str] = field(default_factory=list)


def recent_query(settings: GmailSettings) -> str:
    return f"newer_than:{settings.scan_days}d -in:sent -in:drafts"


def _query(settings: GmailSettings, terms: tuple[str, ...]) -> str:
    quoted = []
    for term in terms:
        normalized = " ".join(term.split())
        if not re.fullmatch(r"[\w -]+", normalized) or len(normalized) > 100:
            raise ValueError("Gmail discovery terms must contain plain words/phrases, not Gmail operators")
        quoted.append('"' + normalized + '"')
    if not quoted or len(quoted) > 30:
        raise ValueError("Gmail discovery terms must contain 1..30 terms")
    query = recent_query(settings) + " {" + " ".join(quoted) + "}"
    return query + (" is:unread" if settings.unread_only else "")


def candidate_query(settings: GmailSettings) -> str:
    """The first, assignment-specific query (kept as a public compatibility helper)."""
    return _query(settings, settings.search_terms)


def discovery_queries(settings: GmailSettings) -> list[str]:
    return list(dict.fromkeys((candidate_query(settings), _query(settings, settings.discovery_terms))))


def discover_messages(messages, settings: GmailSettings, execute) -> DiscoveryResult:
    """Merge bounded query pages, preserving first-seen order and unique IDs."""
    result = DiscoveryResult()
    seen = set()

    def run(query, raw_limit, *, fallback=False):
        report = QueryReport(query, fallback=fallback)
        result.queries.append(report)
        page_token = None
        seen_tokens = set()
        raw_count = 0
        # Bound even pathological responses with endless distinct empty page tokens.
        page_limit = (raw_limit + PAGE_SIZE - 1) // PAGE_SIZE + 2
        while raw_count < raw_limit and len(result.ids) < settings.max_messages and report.pages < page_limit:
            parameters = dict(userId="me", q=query, maxResults=min(
                PAGE_SIZE, raw_limit - raw_count, settings.max_messages - len(result.ids)),
                includeSpamTrash=False, fields="messages/id,nextPageToken")
            if page_token:
                parameters["pageToken"] = page_token
            report.pages += 1
            try:
                page = execute(messages.list(**parameters))
                if not isinstance(page, dict) or not isinstance(page.get("messages", []), list):
                    raise GmailError("Gmail returned an invalid message list. Retry the scan.")
            except GmailError as exc:
                report.failed = True
                result.errors.append(str(exc))
                return
            for item in page.get("messages", []):
                if raw_count >= raw_limit or len(result.ids) >= settings.max_messages:
                    result.truncated = True
                    break
                raw_count += 1
                message_id = item.get("id") if isinstance(item, dict) else None
                if not isinstance(message_id, str) or not message_id:
                    result.errors.append("Gmail returned a malformed message identifier.")
                    continue
                report.ids_returned += 1
                if message_id in seen:
                    result.duplicates += 1
                    continue
                seen.add(message_id)
                result.ids.append(message_id)
                report.unique_added += 1
            page_token = page.get("nextPageToken")
            if not page_token:
                return
            if not isinstance(page_token, str) or page_token in seen_tokens:
                result.errors.append("Gmail returned an invalid/repeated page token; query stopped safely.")
                report.failed = True
                return
            seen_tokens.add(page_token)
        if page_token:
            result.truncated = True

    queries = discovery_queries(settings)
    for query in queries:
        if len(result.ids) >= settings.max_messages:
            result.truncated = True
            break
        run(query, settings.max_messages)
        if result.queries[-1].failed:
            break  # A failed listing is not an empty search; avoid more quota pressure.
    # A listing failure is not evidence of an empty search. Do not hide it with fallback.
    if not result.ids and not result.errors and settings.fallback_messages:
        result.fallback_used = True
        fallback_query = recent_query(settings) + (" is:unread" if settings.unread_only else "")
        run(fallback_query, min(settings.fallback_messages, settings.max_messages), fallback=True)
    return result
