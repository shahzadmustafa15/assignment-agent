"""Bounded, noninteractive scans of an explicitly configured LMS course page.

Only controlled event names and counts enter logs. Browser state and the selected
page remain private files under credentials; no credentials are entered here.
"""
import fcntl
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.lms.base import LMSError, validate_url
from app.lms.extraction import read_assignment_table
from app.lms.parser import public_link
from app.lms.session import browser_options, login_page, read_state, save_state
from app.notifier import DesktopNotifier, NotificationError

AUTH_COOLDOWN = 24 * 60 * 60


class AuthExpired(LMSError):
    pass


def event(name, **counts):
    print(json.dumps({'time': datetime.now(timezone.utc).isoformat(),
                      'event': name, **counts}), flush=True)


def target_path(settings):
    return settings.state_path.with_name('bahria_scan_target.json')


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
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'lms-scan.lock').open('a') as handle:
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

    if student_id:
        state_file = (
            local_settings.data_dir
            / "students"
            / student_id
            / "lms-auth-notice.json"
        )
    else:
        state_file = (
            local_settings.data_dir
            / "lms-auth-notice.json"
        )

    result_code = 1
    event('scan_started', dry_run=dry_run)
    try:
        with scan_lock(local_settings.data_dir) as acquired:
            if not acquired:
                event('scan_skipped_already_running')
                result_code = 1 if report_email else 0
                return result_code
            notice = private_json(state_file) if state_file.exists() else {}
            # Hash only for detecting an explicit reauthentication; never logged.
            fingerprint = hashlib.sha256(settings.state_path.read_bytes()).hexdigest() if settings.state_path.exists() else 'missing'
            now = clock()
            if (not dry_run and notice.get('fingerprint') == fingerprint
                    and now < notice.get('retry_after', 0)):
                event('authentication_required_cooldown')
                result_code = 1 if report_email else 0
                return result_code
            try:
                state = read_state(settings.state_path)
                if state is None:
                    raise AuthExpired('LMS session absent.')
                target = load_target(settings)
                with (playwright_factory or sync_playwright)() as playwright:
                    browser = playwright.chromium.launch(**browser_options(True))
                    try:
                        context = browser.new_context(storage_state=state, accept_downloads=False)
                        context.set_default_timeout(15000)
                        if multi_course or report_email:
                            from app.lms.courses import scan_courses
                            report_options = {'include': '', 'exclude': '', 'notify': False, 'report_details': True} if report_email else {}
                            result = scan_courses(
                                context,
                                target,
                                local_settings.database_path,
                                notifier,
                                dry_run=dry_run,
                                verbose=verbose,
                                discover_only=discover_only,
                                student_id=student_id or "shahzad",
                                **report_options,
                            )
                            if result.get('auth_expired'):
                                raise AuthExpired('LMS session expired during course navigation.')
                        else:
                            table = fetch_table(context, target)
                            event('authentication_valid')
                            result = scan_table(
                                table,
                                local_settings.database_path,
                                notifier,
                                dry_run=dry_run,
                                verbose=verbose,
                                student_id=student_id or "shahzad",
                            )
                        if not dry_run:
                            save_state(settings.state_path, context.storage_state(indexed_db=True))
                        if not dry_run and notice:
                            save_state(state_file, {})
                        event('scan_result', parsed=result['parsed'], new=result['new'],
                              existing=result['existing'], updated=result['updated'],
                              requiring_review=result['review'], errors=len(result['errors']),
                              notified=result['notified'])
                        if report_email:
                            from app.assignment_report import send_assignment_report
                            send_assignment_report(result)
                        result_code = int(bool(result['errors']))
                    finally:
                        browser.close()
            except AuthExpired:
                event('authentication_required')
                if report_email:
                    print('Report not sent: run python -m app.main lms-auth to refresh the session.')
                    result_code = 1
                    return result_code
                if not dry_run:
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
                result_code = 0
    except (LMSError, Error, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        # Never log exception text: Playwright errors can include session URLs.
        event('scan_error', errors=1)
    finally:
        event('scan_completed', exit_code=result_code)
    return result_code
