"""Manual Bahria browser adapter; heading-based assignment table reads only."""
import json
from urllib.parse import urlsplit
from app.lms.base import LMSError, validate_url
from app.lms.parser import inspect_structure, safe_text, safe_url
from app.lms.session import read_state, save_state, session_status, browser_options


class BahriaLMSAdapter:
    def __init__(self, settings, *, playwright_factory=None, prompt=None):
        self.settings = settings
        self.factory = playwright_factory
        self.prompt = prompt or input

    def run(self, command: str, *, dry_run=False, verbose=False, configure_background=False):
        state = None if command == "lms-auth" else read_state(self.settings.state_path)
        if command != "lms-auth" and state is None and not configure_background:
            print("Auth state: absent. Reauthentication required; run python -m app.main lms-auth.")
            return 1
        url = self.settings.portal_url
        if not url and command == "lms-auth":
            try:
                url = self.prompt("Paste the exact HTTPS Bahria portal URL you normally use (not your password): ").strip()
            except (EOFError, KeyboardInterrupt):
                raise LMSError("Authentication cancelled.") from None
        url = validate_url(url)
        try:
            from playwright.sync_api import sync_playwright, Error
        except ImportError:
            raise LMSError("Install project requirements and run python -m playwright install chromium.") from None
        try:
            with (self.factory or sync_playwright)() as playwright:
                browser = playwright.chromium.launch(**browser_options(command == "lms-status"))
                try:
                    context = browser.new_context(storage_state=state, accept_downloads=False)
                    context.set_default_timeout(15000)
                    page = context.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    if response is not None and response.status >= 400:
                        raise LMSError(f"Portal returned HTTP {response.status}; session could not be checked.")
                    if command == "lms-auth":
                        answer = self.prompt("Log in manually in Chromium. Once your authenticated portal is visible, type yes here to save the session: ")
                        if answer.strip().lower() != "yes":
                            print("Authentication cancelled; existing saved state was preserved.")
                            return 1
                        status = session_status(page, url)
                        if not status.startswith("session appears usable"):
                            raise LMSError("Authentication could not be confirmed. Return to the authenticated portal before saving.")
                        from app.lms.background import target_path, configure_target
                        if configure_background or target_path(self.settings).exists():
                            self.prompt("Open Go To LMS, then open one current course Assignments page. Press Enter when ready: ")
                            lms_page = self.select_inspection_page(context, url)
                            from app.lms.extraction import read_assignment_table
                            configure_target(self.settings, context, lms_page, read_assignment_table(lms_page))
                        save_state(self.settings.state_path, context.storage_state(indexed_db=True))
                        save_state(self.settings.state_path.with_name("bahria_portal.json"), {"portal_url": url})
                        print("User-confirmed session saved privately. No password input was collected by this application.")
                    elif command == "lms-status":
                        status = session_status(page, url)
                        print(f"Auth state: exists. {status}.")
                        return 0 if status.startswith("session appears usable") else 1
                    else:
                        status = session_status(page, url)
                        if not status.startswith("session appears usable") and not configure_background:
                            raise LMSError("Reauthentication required or session unverified; run python -m app.main lms-auth.")
                        if configure_background:
                            self.prompt("Log in manually if needed, then open Go To LMS and the course Assignments page. Press Enter when ready: ")
                        else:
                            self.prompt("Navigate to the assignment page (a new LMS tab is fine). Avoid profile pages. Press Enter when ready: ")
                        page = self.select_inspection_page(context, url)
                        if command == "lms-scan":
                            from app.lms.extraction import read_assignment_table
                            from app.lms.scan import scan_table
                            from app.config import load_settings as load_local_settings
                            from app.notifier import DesktopNotifier
                            table = read_assignment_table(page)
                            if configure_background:
                                from app.lms.background import configure_target
                                configure_target(self.settings, context, page, table)
                            result = scan_table(table, load_local_settings().database_path,
                                                DesktopNotifier(), dry_run=dry_run, verbose=verbose)
                            return 1 if result['errors'] else 0
                        print(json.dumps(inspect_structure(page), indent=2, ensure_ascii=True))
                    return 0
                finally:
                    browser.close()
        except (EOFError, KeyboardInterrupt):
            raise LMSError("Browser operation cancelled; saved state was preserved.") from None
        except Error:
            raise LMSError("Browser operation failed (navigation, timeout, or browser unavailable). Check connectivity and Chromium installation, then retry. No browser diagnostics containing session data were printed.") from None


    def select_inspection_page(self, context, portal_url):
        # Yield briefly to Playwright so popup events queued during terminal input arrive.
        for candidate in context.pages:
            if not candidate.is_closed():
                candidate.wait_for_timeout(100)
                break
        pages = [candidate for candidate in context.pages if not candidate.is_closed()]
        if not pages:
            raise LMSError("All browser tabs were closed; run lms-inspect again.")
        if len(pages) > 1:
            print("Open tabs (sanitized titles and addresses):")
            for number, candidate in enumerate(pages, 1):
                print(f"{number}: " + json.dumps({"title": safe_text(candidate.title()),
                                               "url": safe_url(candidate.url)}, ensure_ascii=True))
            answer = self.prompt("Enter the number of the LMS assignment tab (blank cancels): ").strip()
            if not answer.isdecimal() or not 1 <= int(answer) <= len(pages):
                raise LMSError("No valid tab selected; inspection cancelled.")
            page = pages[int(answer) - 1]
        else:
            page = pages[0]
        page.bring_to_front()
        page.wait_for_load_state("domcontentloaded")
        selected_url = page.url
        validate_url(urlsplit(selected_url)._replace(fragment="").geturl())
        def origin(value):
            parts = urlsplit(value)
            return parts.scheme, parts.hostname, parts.port or 443
        if origin(selected_url) != origin(portal_url):
            print("Selected tab uses a different origin: " + json.dumps(safe_url(selected_url)))
            answer = self.prompt("Confirm this is your Bahria LMS page and you are logged in. Inspect this tab once? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                raise LMSError("Different-origin inspection cancelled.")
        if (origin(page.url) != origin(selected_url)
                or not session_status(page, selected_url).startswith("session appears usable")):
            raise LMSError("Selected tab changed origin or requires login; inspection cancelled.")
        return page
