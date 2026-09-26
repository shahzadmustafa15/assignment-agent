"""Verified Bahria download-link evidence; never request or execute these URLs."""
import base64
import binascii
import re
from urllib.parse import urljoin, urlsplit, parse_qs


def download_evidence(href: str, page_url: str) -> dict | None:
    """Read the observed Download.php?k= envelope, without storing its opaque key.

    Observed fields: numeric prefix, date, category, numeric field, filename,
    return URL. The numeric fields have not been verified as assignment IDs.
    """
    try:
        target = urlsplit(urljoin(page_url, href))
        origin = urlsplit(page_url)
        if (target.scheme != 'https' or target.hostname != origin.hostname
                or target.port != origin.port or target.username or target.password
                or target.path != '/Student/Download.php'):
            return None
        query = parse_qs(target.query, strict_parsing=True)
        if set(query) != {'k'} or len(query['k']) != 1:
            return None
        key = query['k'][0]
        if not key or len(key) > 8192:
            return None
        fields = base64.b64decode(key + '=' * (-len(key) % 4), validate=True).decode('utf-8').split(',', 5)
        if (len(fields) != 6 or not fields[0].isdigit() or not fields[3].isdigit()
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fields[1])
                or fields[2] not in {'Assignment', 'AssignmentSubmissions'}):
            return None
        filename = fields[4]
        if (not filename or len(filename) > 255 or re.search(r'[\x00-\x1f/\\]', filename)
                or filename in {'.', '..'} or not re.search(r'\.[A-Za-z0-9]{1,10}$', filename)):
            return None
        return {'category': fields[2], 'filename': filename,
                'endpoint': target._replace(query='', fragment='').geturl()}
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        return None


def submission_evidence(cell, page_url):
    text = ' '.join(cell.get('text', '').split())
    negative = bool(re.search(r'\b(?:no submission|not submitted)\b', text, re.I))
    files = []
    for link in cell.get('links', []):
        if not isinstance(link, dict) or link.get('has_handler'):
            continue
        evidence = download_evidence(link.get('href', ''), page_url)
        if evidence and evidence['category'] == 'AssignmentSubmissions':
            files.append(evidence)
    explicit = bool(re.fullmatch(r'(?:submitted|submission received|successfully submitted)(?:\s+on\s+.+)?', text, re.I))
    if negative:
        return False, 'Explicit no-submission text' + ('; conflicting file evidence requires review' if files else ''), bool(files)
    if files:
        return True, 'Added Submission contains a verified AssignmentSubmissions download with a filename', False
    if explicit:
        return True, 'Explicit affirmative submission status', False
    return False, 'No verified submission; label or filename text alone is insufficient', True


def action_evidence(cell, page_url):
    if not isinstance(cell, dict):
        return {'type': 'malformed', 'url': None, 'id': None, 'verified': False}
    links = cell.get('links', [])
    buttons = cell.get('buttons', [])
    forms = cell.get('forms', [])
    if not isinstance(links, list) or not isinstance(buttons, list) or not isinstance(forms, list):
        return {'type': 'malformed', 'url': None, 'id': None, 'verified': False}
    kind = 'text'
    if forms:
        kind = 'form (not executed)'
    elif buttons:
        kind = 'button (not clicked)'
    elif links:
        if any(not isinstance(link, dict) for link in links):
            return {'type': 'malformed', 'url': None, 'id': None, 'verified': False}
        kind = 'JavaScript (not executed)' if any(link.get('has_handler') or str(link.get('href', '')).lower().startswith('javascript:') for link in links) else 'anchor (unverified; not followed)'
    verified = kind == 'text' and cell.get('text', '').strip().casefold() == 'deadline exceeded'
    # Real inspected Action cells are plain text, without IDs or detail links.
    # Never reinterpret a download key as an assignment ID or detail URL.
    return {'type': kind, 'url': None, 'id': None, 'verified': verified}
