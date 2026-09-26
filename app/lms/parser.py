"""Safe structural inspection and conservative assignment-row parsing."""
import re
from urllib.parse import urlsplit, urlunsplit


def safe_text(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\S+@\S+", "[email redacted]", value)
    value = re.sub(r"\b\d[\d/-]{4,}\b", "[identifier redacted]", value)
    value = re.sub(r"\b[A-Za-z0-9_-]{24,}\b", "[value redacted]", value)
    if re.search(r"password|token|secret|student name|registration|welcome|logged in|roll no", value, re.I):
        return "[personal or authentication label omitted]"
    return value.strip()[:160]


def safe_url(value: str) -> str:
    parts = urlsplit(value)
    # Path segments can contain student IDs or session tokens; origin is sufficient here.
    return urlunsplit((parts.scheme, parts.hostname or "", "/[path omitted]", "", ""))


def inspect_structure(page) -> dict:
    result = {"title": safe_text(page.title()), "url": safe_url(page.url)}
    for label, selector in {
        "headings": "h1,h2,h3,h4,h5,h6,[role=heading]",
        "navigation": "nav a,[role=navigation] a",
        "links": "a",
        "buttons": "button,[role=button]",
        "table_headings": "th,[role=columnheader]",
    }.items():
        elements = page.locator(selector)
        values = []
        for index in range(min(elements.count(), 100)):
            item = elements.nth(index)
            if item.is_visible():
                text = safe_text(item.inner_text())
                if text and text not in values:
                    values.append(text)
        result[label] = values
    return result


# Assignment parsing is deliberately independent of the browser and database.
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from urllib.parse import urljoin, parse_qsl
from app.assignment_parser import extract_deadline
from app.models import Assignment, Source, Status, LOCAL_TIMEZONE, normalized, utc_now
from app.lms.extraction import HEADERS
from app.lms.evidence import download_evidence, submission_evidence, action_evidence


@dataclass(frozen=True)
class LMSRecord:
    assignment: Assignment
    metadata: dict


def public_link(value: str, base_url: str) -> str | None:
    """Retain only HTTPS links without authentication material; never execute them."""
    try:
        if not value or value.startswith('#'):
            return None
        parsed = urlsplit(urljoin(base_url, value))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        if any(key.casefold() == 'k' or re.search(r'token|session|auth|password|secret|ticket|signature|saml|code', key, re.I)
               or len(value) > 128 for key, value in parse_qsl(parsed.query)):
            return None
        return parsed._replace(fragment='').geturl()
    except ValueError:
        return None


def parse_lms_deadline(text: str):
    if not isinstance(text, str):
        return None
    # Real portal format: "16 September 2026-09:00 am". Multiple dates
    # (as seen in Lab 01) are ambiguous without verified revision semantics.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        values = [parse_lms_deadline(line) for line in lines]
        return values[0] if values[0] is not None and all(v == values[0] for v in values) else None
    text = ' '.join(text.split())
    portal = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})\s*-\s*(\d{1,2}:\d{2})\s*(am|pm)", text, re.I)
    if portal:
        value = f"{portal[1]} {portal[2]} {portal[3]} {portal[4]} {portal[5].upper()}"
        try:
            return datetime.strptime(value, '%d %B %Y %I:%M %p').replace(tzinfo=LOCAL_TIMEZONE)
        except ValueError:
            return None
    # ISO with explicit time; a date alone must not become a made-up midnight.
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?', text):
        try:
            value = datetime.fromisoformat(text.replace('Z', '+00:00'))
            return value if value.tzinfo else value.replace(tzinfo=LOCAL_TIMEZONE)
        except ValueError:
            return None
    return extract_deadline('Deadline: ' + text)



def resolve_deadline(cell):
    """Select by explicit evidence, never by DOM order or warning/info colors."""
    raw = cell.get('raw_text', cell.get('text', ''))
    sources = cell.get('deadline_sources')
    if sources is None:
        sources = [{'raw': line.strip(), 'hidden': False, 'context': []}
                   for line in raw.splitlines() if line.strip()]
    candidates = []
    explicit_labels = {'actual', 'deadline', 'assignment deadline', 'due date'}
    for source in sources:
        value = source.get('raw', '').strip()
        context = source.get('context', [])
        labels = [v.strip() for element in context for v in
                  (element.get('title', ''), element.get('label', '')) if v.strip()]
        inline = re.match(r'^(Actual|Assignment deadline|Deadline|Due date)\s*:\s*(.+)$', value, re.I)
        if inline:
            labels.append(inline[1])
        parsed = parse_lms_deadline(inline[2] if inline else value)
        candidates.append({'raw': value, 'hidden': bool(source.get('hidden')), 'context': context,
                           'labels': labels, 'parsed': parsed,
                           'explicit': any(label.casefold() in explicit_labels for label in labels),
                           'conditional': any('extended only for missing' in label.casefold() for label in labels)})
    visible = [c for c in candidates if not c['hidden']]
    valid = [c for c in visible if c['parsed'] is not None]
    distinct = {c['parsed'] for c in valid}
    selected = None
    reason = 'No reliably parsed visible deadline.'
    explicit = [c for c in valid if c['explicit'] and not c['conditional']]
    # A malformed explicitly-labeled date must not be bypassed by a secondary time.
    invalid_explicit = any(c['explicit'] and c['parsed'] is None for c in visible)
    if explicit and not invalid_explicit and len({c['parsed'] for c in explicit}) == 1:
        selected = explicit[0]['parsed']
        reason = 'Selected the unique date explicitly labeled Actual/Deadline in the Deadline cell.'
    elif (len(distinct) == 1 and len(valid) == len(visible) and not invalid_explicit
          and not any(c['conditional'] for c in visible)):
        selected = valid[0]['parsed']
        reason = 'All visible deadline candidates agree; hidden markup was excluded.'
    elif len(distinct) > 1:
        reason = 'Different visible deadline values lack a unique reliable deadline label; selection refused.'
    conditional = any(c['conditional'] for c in visible)
    serialized = [{**c, 'parsed': c['parsed'].astimezone(LOCAL_TIMEZONE).isoformat() if c['parsed'] else None}
                  for c in candidates]
    return selected, {'deadline_raw_values': list(dict.fromkeys(c['raw'] for c in candidates)),
                      'deadline_candidates': serialized, 'deadline_conflict': len(distinct) > 1,
                      'deadline_selection_reason': reason,
                      'deadline_conditional_extension': conditional}

def parse_assignment_row(headers, cells, *, course='', semester='', page_url='', now=None):
    if len(cells) == 1 and int(cells[0].get('colspan', 1)) == len(headers):
        message = normalized(cells[0].get('text', ''))
        if re.search(r'\b(no|not|empty)\b', message):
            return None  # The real empty-table shape is one cell spanning eight columns.
        raise ValueError('Unrecognized spanning row; inspect table before importing')
    if len(cells) != len(headers) or any(int(c.get('colspan', 1)) != 1 or int(c.get('rowspan', 1)) != 1 for c in cells):
        raise ValueError('Malformed assignment row or unsupported merged cells')
    if len(headers) != len(HEADERS) or set(headers) != set(HEADERS):
        raise ValueError('Unexpected assignment table headings')
    values = dict(zip(headers, cells))
    title = values['Title'].get('text', '').strip()
    if not title:
        raise ValueError('Missing assignment title; row skipped')
    course = ' '.join(course.split())
    if not course or re.search(r'^(?:--|select\b|all courses\b|choose\b)', course, re.I):
        course = 'Unknown course'
    deadline_text = values['Deadline'].get('raw_text', values['Deadline'].get('text', ''))
    deadline, deadline_evidence = resolve_deadline(values['Deadline'])
    submission = values['Added Submission'].get('text', '').strip()
    submitted, evidence, uncertain_submission = submission_evidence(values['Added Submission'], page_url)
    action = action_evidence(values['Action'], page_url)
    status = Status.SUBMITTED if submitted else Status.NEW
    if deadline and not submitted and deadline < (now or utc_now()):
        status = Status.OVERDUE
    metadata = {'assignment_number': values['Assign. No.'].get('text', ''),
        'submission_remarks': values['Assignment (Solution File) Remarks'].get('text', ''),
        'added_submission': submission, 'marks': values['Marks Obtained'].get('text', ''),
        'returned_comments': values['Returned Submission (Comments)'].get('text', ''),
        'deadline_text': deadline_text, 'semester': semester, 'links': {},
        'action_buttons': values['Action'].get('buttons', []),
        'action': action, 'submission_evidence': evidence, 'attachments': [],
        'identity_strategy': 'fallback course/title/deadline hash; no verified LMS ID',
        'review_reasons': [], **deadline_evidence}
    for heading, cell in values.items():
        links = []
        for link in cell.get('links', []) if isinstance(cell.get('links', []), list) else []:
            if not isinstance(link, dict):
                continue
            file_evidence = download_evidence(link.get('href', ''), page_url)
            if file_evidence and file_evidence['category'] == 'Assignment' and heading == 'Assignment (Solution File) Remarks':
                metadata['attachments'].append(file_evidence)
            url = public_link(link.get('href', ''), page_url)
            links.append({'text': link.get('text', ''), 'url': url,
                          'has_handler': bool(link.get('has_handler'))})
        if links:
            metadata['links'][heading] = links
    if not action['verified']:
        metadata['review_reasons'].append('Unverified Action structure; no control was clicked.')
    if uncertain_submission:
        metadata['review_reasons'].append('Submission state is uncertain or conflicting.')
    # No assignment ID or detail link exists in the observed Action cells.
    if deadline_evidence['deadline_conditional_extension']:
        metadata['review_reasons'].append('Conditional extension is preserved separately; its applicability needs review.')
    if course == 'Unknown course':
        metadata['review_reasons'].append('Course is unknown.')
    if deadline is None:
        metadata['review_reasons'].append('Deadline date/time is missing or ambiguous.')
    identity = [normalized(course), normalized(title)]
    if deadline is not None:
        identity.append(deadline.astimezone(LOCAL_TIMEZONE).isoformat())
        metadata['identity_strategy'] = 'course/title/confident deadline hash'
        prefix = 'bahria:fallback:'
    else:
        identity.extend([normalized(metadata['assignment_number']), normalized(semester)])
        metadata['identity_strategy'] = 'course/title/assignment number/semester hash; unresolved deadline excluded'
        prefix = 'bahria:unresolved:'
    canonical = json.dumps(identity, ensure_ascii=True)
    external_id = prefix + hashlib.sha256(canonical.encode()).hexdigest()
    assignment = Assignment(course=course, title=title, source=Source.LMS,
        external_message_id=external_id, deadline=deadline, status=status, needs_review=bool(metadata['review_reasons']))
    return LMSRecord(assignment, metadata)


def parse_assignment_table(table, *, now=None):
    records, errors = [], []
    for index, row in enumerate(table['rows'], 1):
        try:
            record = parse_assignment_row(table['headers'], row, course=table.get('course', ''),
                semester=table.get('semester', ''), page_url=table.get('page_url', ''), now=now)
            if record is not None:
                records.append(record)
        except (ValueError, TypeError, KeyError, AttributeError):
            errors.append(f'Row {index}: malformed or incomplete structure; skipped.')
    return records, errors
