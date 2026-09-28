"""Bounded, noninteractive scans of an explicitly configured LMS course page.

Only controlled event names and counts enter logs. Browser state and the selected
page remain private files under credentials; no credentials are entered here.
"""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin

from app.lms.base import LMSError, validate_url
from app.lms.extraction import read_assignment_table
from app.lms.parser import public_link
from app.lms.session import browser_context, login_page, read_state, save_state, has_session, private_directory, playwright_session
from app.notifier import DesktopNotifier, NotificationError

AUTH_COOLDOWN = 24 * 60 * 60


class AuthExpired(LMSError):
    pass


def event(name, **counts):
    print(json.dumps({'time': datetime.now(timezone.utc).isoformat(),
                      'event': name, **counts}), flush=True)


def target_path(settings):
    return settings.state_path.with_name('bahria_scan_target.json')


def auth_notice_path(local_settings, student_id=None):
    directory = local_settings.data_dir
    if student_id:
        directory = directory / 'students' / student_id
    return directory / 'lms-auth-notice.json'


def clear_auth_notice(local_settings, student_id=None):
    """Explicit reset, even when reauthentication returns identical cookies."""
    save_state(auth_notice_path(local_settings, student_id), {})


def recover_session(context, settings):
    """One read-only CMS-to-LMS handoff using the portal's actual link.

    Never submit a login form, infer a token URL, or retry a login. Only Bahria
    HTTPS origins are eligible; an absent/ambiguous link requires manual auth.
    """
    portal = urlsplit(settings.portal_url)
    if portal.scheme != 'https' or not (portal.hostname or '').endswith('.bahria.edu.pk'):
        return False
    page = context.new_page()
    try:
        response = page.goto(settings.portal_url, wait_until='domcontentloaded', timeout=45000)
        check_auth(page, response)
        if urlsplit(page.url).netloc != portal.netloc:
            return False
        links = page.get_by_role('link', name=re.compile(r'^\s*Go To LMS\s*$', re.I))
        visible = [links.nth(i) for i in range(links.count()) if links.nth(i).is_visible()]
        if len(visible) != 1:
            return False
        href = visible[0].get_attribute('href')
        if not href:
            return False
        url = urljoin(page.url, href)
        destination = urlsplit(url)
        if (destination.scheme != 'https' or destination.username or destination.password
                or not (destination.hostname or '').endswith('.bahria.edu.pk')):
            return False
        response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
        check_auth(page, response)
        return urlsplit(page.url).hostname == 'lms.bahria.edu.pk'
    except AuthExpired:
        return False
    finally:
        page.close()


def private_json(path):
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise LMSError('LMS background configuration must be private.')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise LMSError('Invalid LMS background configuration.')
    return value


def check_auth(page, response=None):
    if (response is not None and response.status in (401, 403)) or login_page(page):
        raise AuthExpired('LMS login required.')
    # CMS can present a Sign In landing page without a password field.
    signin = page.get_by_role('link', name='Sign In', exact=True)
    if signin.count() and signin.first.is_visible():
        raise AuthExpired('LMS login required.')
    if response is not None and response.status >= 400:
        raise LMSError('LMS returned an HTTP error; no retry this run.')


def load_target(settings):
    target = private_json(target_path(settings))
    url = validate_url(target['url'])
    if urlsplit(url).hostname != 'lms.bahria.edu.pk' or public_link(url, url) != url:
        raise LMSError('Unverified LMS background target.')
    if not target.get('course') or not target.get('semester'):
        raise LMSError('LMS background target requires a verified course and semester.')
    return target


def fetch_table(context, target):
    """Replay only a verified read-only page URL; never click assignment actions."""
    page = context.new_page()
    try:
        response = page.goto(target['url'], wait_until='domcontentloaded', timeout=45000)
        check_auth(page, response)
        if urlsplit(page.url).netloc != urlsplit(target['url']).netloc:
            raise AuthExpired('LMS session redirected outside the configured origin.')
        table = read_assignment_table(page)
        if table['course'] != target['course'] or table['semester'] != target['semester']:
            raise LMSError('Saved LMS URL no longer selects the verified course/semester; reconfigure it.')
        return table
    finally:
        page.close()


def configure_target(settings, context, page, table):
    """Verify URL replay before storing a background target or refreshed state."""
    url = page.url
    if (urlsplit(url).hostname != 'lms.bahria.edu.pk'
            or public_link(url, url) != url or not table['course'] or not table['semester']):
        raise LMSError('Selected LMS page is not a safe replayable course URL.')
    target = {'url': url, 'course': table['course'], 'semester': table['semester']}
    # The user-selected table already verified this session. Retain it even if
    # URL replay fails so setup can be repaired without another login.
    save_state(settings.state_path, context.storage_state(indexed_db=True))
    fetch_table(context, target)
    save_state(target_path(settings), target)
    print('Private background course target saved and replay verified. No timer was installed or enabled.')


@contextmanager
def scan_lock(directory):
    private_directory(directory)
    fd = os.open(directory / 'lms-scan.lock', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_background(settings, local_settings, *, dry_run=False, notifier=None,
                   playwright_factory=None, clock=time.time, multi_course=False,
                   discover_only=False, verbose=False, report_email=False,
                   student_id=None):
    from playwright.sync_api import sync_playwright, Error
    from app.lms.scan import scan_table
    notifier = notifier or DesktopNotifier()

    state_file = auth_notice_path(local_settings, student_id)

    result_code = 1
    event('scan_started', dry_run=dry_run)
    try:
        lock_directory = settings.state_path.parent if student_id else local_settings.data_dir
        with scan_lock(lock_directory) as acquired:
            if not acquired:
                event('scan_skipped_already_running')
                result_code = 1 if report_email or student_id else 0
                return result_code
            notice = private_json(state_file) if state_file.exists() else {}
            # Hash only for detecting an explicit reauthentication; never logged.
            fingerprint = hashlib.sha256(settings.state_path.read_bytes()).hexdigest() if settings.state_path.exists() else 'missing'
            now = clock()
            if (not dry_run and notice.get('fingerprint') == fingerprint
                    and now < notice.get('retry_after', 0)):
                event('authentication_required_cooldown')
                result_code = 1 if report_email or student_id else 0
                return result_code
            try:
                if not has_session(settings):
                    raise AuthExpired('LMS session absent.')
                state = read_state(settings.state_path)
                target = load_target(settings)
                with playwright_session(playwright_factory or sync_playwright) as playwright:
                    with browser_context(playwright, settings, headless=True, state=state) as context:
                        context.set_default_timeout(15000)
                        for attempt in range(2):
                            try:
                                if multi_course or report_email:
                                    from app.lms.courses import scan_courses
                                    report_options = {'include': '', 'exclude': '', 'notify': False, 'report_details': True} if report_email else {}
                                    result = scan_courses(
                                        context, target, local_settings.database_path, notifier,
                                        dry_run=dry_run, verbose=verbose, discover_only=discover_only,
                                        student_id=student_id or "shahzad", **report_options,
                                    )
                                    if result.get('auth_expired'):
                                        raise AuthExpired('LMS session expired during course navigation.')
                                else:
                                    table = fetch_table(context, target)
                                    event('authentication_valid')
                                    result = scan_table(
                                        table, local_settings.database_path, notifier,
                                        dry_run=dry_run, verbose=verbose,
                                        student_id=student_id or "shahzad",
                                    )
                                break
                            except AuthExpired:
                                if attempt or not settings.profile_path or not recover_session(context, settings):
                                    raise
                                event('authentication_recovery_attempted')
                        if not dry_run:
                            save_state(settings.state_path, context.storage_state(indexed_db=True))
                        if not dry_run and notice:
                            save_state(state_file, {})
                        if not dry_run and student_id:
                            from app.database import Database
                            from app.students import set_auth_status
                            set_auth_status(Database(local_settings.database_path), student_id, 'ACTIVE')
                        event('scan_result', parsed=result['parsed'], new=result['new'],
                              existing=result['existing'], updated=result['updated'],
                              requiring_review=result['review'], errors=len(result['errors']),
                              notified=result['notified'])
                        if report_email:
                            from app.assignment_report import send_assignment_report
                            send_assignment_report(result)
                        result_code = int(bool(result['errors']))
            except AuthExpired:
                event('authentication_required')
                if report_email:
                    print('Report not sent: run python -m app.main lms-auth to refresh the session.')
                    result_code = 1
                    return result_code
                if not dry_run:
                    if student_id:
                        from app.database import Database
                        from app.students import set_auth_status
                        set_auth_status(Database(local_settings.database_path), student_id, 'LOGIN_REQUIRED')
                    last = notice.get('notified_at', 0)
                    if not last or now - last >= AUTH_COOLDOWN:
                        try:
                            command = (
                                f'python -m app.main student-auth {student_id}'
                                if student_id
                                else 'python -m app.main lms-auth'
                            )

                            notifier.send(
                                'Bahria LMS login required',
                                f'Your saved LMS session expired. Run: {command}'
                            )
                            last = now
                        except NotificationError:
                            event('authentication_notification_failed')
                    from app.email_notifications import notify_auth_expired
                    notify_auth_expired(
                        local_settings.database_path,
                        fingerprint,
                        now,
                        student_id=student_id,
                    )
                    save_state(state_file, {'fingerprint': fingerprint,
                               'retry_after': now + AUTH_COOLDOWN, 'notified_at': last})
                result_code = 1 if student_id else 0
    except (LMSError, Error, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        # Never log exception text: Playwright errors can include session URLs.
        event('scan_error', errors=1)
    finally:
        event('scan_completed', exit_code=result_code)
    return result_code
