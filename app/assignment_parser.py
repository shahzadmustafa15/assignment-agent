"""Conservative, local-only MIME and assignment extraction; never execute content."""

import base64
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from fnmatch import fnmatchcase
from html.parser import HTMLParser
import re
from urllib.parse import urlparse

from app.config import GmailSettings
from app.models import Assignment, LOCAL_TIMEZONE, Source

MAX_BODY_BYTES = 1_000_000


class MalformedEmailError(ValueError):
    pass


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if not self.hidden:
            if tag in ("br", "p", "div", "li", "tr"):
                self.text.append("\n")
            if tag == "a":
                href = dict(attrs).get("href", "")
                if safe_url(href):
                    self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in ("p", "div", "li", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def safe_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def _headers(payload: dict) -> dict[str, str]:
    result = {}
    for item in payload.get("headers", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str):
            result[item["name"].lower()] = str(make_header(decode_header(item["value"])))
    return result


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    subject: str
    sender: str
    received_at: datetime | None
    body: str
    links: tuple[str, ...]
    headers: dict[str, str]


def parse_email(message: dict, attachment_loader=None) -> ParsedEmail:
    """Decode inline MIME text, preferring plain text alternatives; ignore attachments.

    Gmail may store even an inline text body behind an attachmentId. Only unnamed
    text/plain and text/html parts may be retrieved through the provided loader.
    """
    try:
        message_id = message["id"]
        payload = message.get("payload", {})
        if not isinstance(message_id, str) or not message_id or not isinstance(payload, dict):
            raise ValueError
        headers = _headers(payload)
        budget = [0, 0]

        def visit(part, depth=0):
            budget[1] += 1
            if not isinstance(part, dict) or depth > 20 or budget[1] > 200:
                raise ValueError("MIME limits")
            part_headers = _headers(part)
            if part.get("filename") or part_headers.get("content-disposition", "").lower().startswith("attachment"):
                return "", []
            mime = part.get("mimeType", "").lower()
            if mime.startswith("multipart/"):
                children = part.get("parts", [])
                decoded = [(child.get("mimeType"), visit(child, depth + 1)) for child in children]
                if mime == "multipart/alternative":
                    plain = [value for kind, value in decoded if kind == "text/plain" and value[0].strip()]
                    if plain:
                        return plain[0][0], [link for _, (_, links) in decoded for link in links]
                return "\n".join(value[0] for _, value in decoded), [link for _, (_, links) in decoded for link in links]
            if mime not in ("text/plain", "text/html"):
                return "", []
            body = part.get("body", {})
            data = body.get("data", "")
            if not data and body.get("attachmentId") and attachment_loader:
                if int(body.get("size", 0)) > MAX_BODY_BYTES:
                    raise ValueError("Body too large")
                data = attachment_loader(body["attachmentId"])
            if not isinstance(data, str) or len(data) > MAX_BODY_BYTES * 2:
                raise ValueError("Body too large")
            raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
            budget[0] += len(raw)
            if budget[0] > MAX_BODY_BYTES:
                raise ValueError("Body too large")
            content_type = Message()
            content_type["content-type"] = part_headers.get("content-type", mime)
            text = raw.decode(content_type.get_content_charset() or "utf-8", errors="replace")
            if mime == "text/html":
                parser = _HTMLText()
                parser.feed(text)
                return "".join(parser.text), parser.links
            return text, []

        body, links = visit(payload)
        received = None
        if message.get("internalDate") is not None:
            received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
        links.extend(re.findall(r"https?://[^\s<>\"']+", body))
        return ParsedEmail(message_id, headers.get("subject", ""), headers.get("from", ""),
                           received, body.strip(), tuple(dict.fromkeys(link for link in links if safe_url(link))), headers)
    except (ValueError, TypeError, KeyError, AttributeError, LookupError, OverflowError, OSError) as exc:
        raise MalformedEmailError("Malformed email or unsupported MIME body") from exc


def sender_allowed(sender: str, settings: GmailSettings) -> bool:
    _, address = parseaddr(sender)
    address = address.lower()
    configured = bool(settings.university_domain or settings.lms_sender or settings.teacher_emails or settings.sender_patterns)
    if not configured:
        return True
    domain = address.rpartition("@")[2]
    return bool(
        (settings.university_domain and (domain == settings.university_domain or domain.endswith("." + settings.university_domain)))
        or (settings.lms_sender and address == settings.lms_sender)
        or address in {item.lower() for item in settings.teacher_emails}
        or any(fnmatchcase(address, pattern.lower()) for pattern in settings.sender_patterns)
    )


def is_assignment_email(email: ParsedEmail, settings: GmailSettings) -> bool:
    # Retain the public boolean API while using the explainable classifier.
    from app.assignment_classifier import classify_email
    return classify_email(email, settings).classification == "ASSIGNMENT"


_MONTHS = "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
_DATE = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|(?:{_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}})"
_TIME = r"(?:\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?|\d{1,2}\s*(?:AM|PM))"
_DEADLINE = re.compile(rf"\b(?:deadline|due(?:\s+date)?|submit\s+by|submission\s+(?:deadline|date))\s*(?:is\s+|on\s+|:\s*)?(?P<date>{_DATE})(?:[ T,]+(?:at\s+)?)(?P<time>{_TIME})(?P<zone>[ \t]*\(?(?:Asia/Karachi|PKT|UTC|GMT|Z|[+-]\d{{2}}:\d{{2}}|[A-Z]{{2,5}})\)?)?", re.I)


def extract_deadline(text: str) -> datetime | None:
    """Require an explicit year, date and time; conflicting/ambiguous dates stay unknown."""
    candidates = set()
    for match in _DEADLINE.finditer(text):
        date_text = match["date"].replace(",", "")
        date = None
        for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
            try:
                date = datetime.strptime(date_text, fmt)
                break
            except ValueError:
                pass
        if date is None:
            return None
        clock = re.sub(r"\s+", "", match["time"]).upper()
        parsed_time = None
        for fmt in ("%I:%M:%S%p", "%I:%M%p", "%I%p", "%H:%M:%S", "%H:%M"):
            try:
                parsed_time = datetime.strptime(clock, fmt).time()
                break
            except ValueError:
                pass
        if parsed_time is None:
            return None
        zone_text = (match["zone"] or "").strip().strip("()").upper()
        if zone_text in ("", "PKT", "ASIA/KARACHI"):
            zone = LOCAL_TIMEZONE
        elif zone_text in ("UTC", "GMT", "Z"):
            zone = timezone.utc
        elif re.fullmatch(r"[+-]\d{2}:\d{2}", zone_text):
            hours, minutes = map(int, zone_text[1:].split(":"))
            if hours > 23 or minutes > 59:
                return None
            zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if zone_text[0] == "+" else -1)))
        else:
            return None
        candidates.add(datetime.combine(date.date(), parsed_time, zone).astimezone(timezone.utc))
    return next(iter(candidates)) if len(candidates) == 1 else None


def _field(text: str, labels: str) -> str:
    match = re.search(rf"^\s*(?:{labels})\s*:\s*([^\n]+)", text, re.I | re.M)
    return match[1].strip() if match else ""


def assignment_from_email(email: ParsedEmail) -> Assignment:
    text = email.subject + "\n" + email.body
    course = _field(email.body, "course(?: name)?|subject name")
    if not course:
        bracket = re.match(r"\[([^\]]+)\]", email.subject)
        course = bracket[1].strip() if bracket else "Unknown course"
    title = _field(email.body, "assignment(?: title)?|title") or email.subject or f"Untitled email ({email.message_id})"
    teacher = _field(email.body, "teacher|instructor|professor")
    lms_links = [link for link in email.links if re.search(r"lms|moodle|classroom|assignment|course|submit", link, re.I)]
    # Email receipt is not evidence of the assignment's LMS upload time.
    received = email.received_at.isoformat() if email.received_at else "Unknown"
    description = f"Email subject: {email.subject}\nSender: {email.sender}\nReceived: {received}\n\n{email.body}"
    return Assignment(course=course, title=title, teacher=teacher, description=description,
                      deadline=extract_deadline(text), lms_url=lms_links[0] if len(lms_links) == 1 else None,
                      source=Source.GMAIL, external_message_id=email.message_id, needs_review=True)
