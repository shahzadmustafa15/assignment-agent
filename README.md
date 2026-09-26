# Assignment Monitoring System

A local Python application for tracking university assignments on Ubuntu.
Phase 4 adds manual read-only Gmail OAuth and assignment email detection to the
existing SQLite storage, CLI, local reminders, and daily summary. Python 3.10 or
newer is required. Gmail uses official Google client libraries; tests use pytest.

**Gmail requires manual Google Cloud setup before account access. Follow
[docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md), then run `python -m app.main gmail-auth`.**

## Setup and run

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app.main
```

Startup creates `data/assignments.db` and the log directory automatically.
It does not seed records or connect to external services. Imports perform no
network access or filesystem writes.

## CLI

```bash
python -m app.main assignments
python -m app.main new
python -m app.main upcoming
python -m app.main overdue
python -m app.main details 1
python -m app.main mark-submitted 1
python -m app.main delete 1
python -m app.main delete 1 --yes
python -m app.main check-reminders
python -m app.main test-notification
python -m app.main daily-summary
python -m app.main daily-summary --notify
```

- `assignments`: list all records, sorted by deadline, with unknown deadlines last.
- `new`: list records with status `NEW`; this does not create an assignment.
- `upcoming`: list unsubmitted records due now or later, with no upper date limit.
- `overdue`: persist `OVERDUE` for deadlines strictly before now, except records
  already `SUBMITTED` or `OVERDUE`, then list overdue records.
- `details ID`: display every field, with timestamps in Asia/Karachi.
- `mark-submitted ID`: update local status only, recording a submission the student
  has already completed. It does not upload files or submit anything externally.

`delete ID` prompts for confirmation, defaulting to No. Only `y` or `yes`
(case-insensitive) confirms; `--yes` skips the prompt. Empty input, EOF, or Ctrl+C
cancels safely. Deletion affects local SQLite storage only and uses existing
foreign-key cleanup for related local reminder history. Gmail processing markers
remain, preventing a deleted assignment from being re-imported on the next scan.
No Gmail message, LMS content, or external service is modified.

Missing IDs produce an error and exit code 1. Read commands do not automatically
change statuses. `check-reminders` also updates overdue statuses. If an overdue deadline is extended, explicitly update its status
as well; the repository does not infer a previous workflow state.

## Assignment model and storage API

`app.models.Assignment` includes `id`, `course`, `title`, `teacher`, `description`,
`uploaded_at`, `deadline`, `lms_url`, `source`, `external_message_id`, `status`,
`needs_review`, `created_at`, and `updated_at`. Course and title are required.
New records default to `NEW`, source `manual`, and `needs_review=True`.

Statuses: `NEW`, `REVIEWED`, `IN_PROGRESS`, `READY_TO_SUBMIT`, `SUBMITTED`, `OVERDUE`.
Sources: `gmail`, `lms`, `manual`. Gmail imports are implemented; LMS remains a source label only.

All datetime values must be timezone-aware. Naive datetimes are rejected to avoid
silent deadline mistakes. SQLite stores canonical UTC timestamps with fixed
microsecond precision. CLI output uses `Asia/Karachi` (UTC+05:00). Unknown deadlines
are allowed but excluded from upcoming and automatic overdue detection.

`app.database.Database(path)` initializes an SQLite file and provides:

- `create_assignment(assignment)` → saved assignment with generated ID/timestamps.
- `get_assignment(id)` → assignment or `None`.
- `list_assignments(status=None)` and `list_by_status(status)` → sorted records.
- `update_assignment(id, **changes)` → validated updated record.
- `mark_submitted(id)` → locally updated record.
- `delete_assignment(id)` → whether a record was deleted.
- `assignment_exists(assignment)` → duplicate identity check.
- `mark_overdue(now=None)` → number of changed records.
- `upcoming(now=None)` → unsubmitted records due at or after the supplied time.

Updates preserve ID and creation time. Missing updates raise
`AssignmentNotFoundError`. SQL values are parameterized, connections close after
use, and writes are transactional. Update validation or uniqueness failure rolls
back the change. No real university records are included.

### Duplicate identity

Database-enforced unique constraints guard against concurrent duplicate writes:

1. A nonblank external message ID is unique within its source. IDs remain
   case-sensitive; outer whitespace is removed.
2. Course + title + deadline is also unique across sources. Course and title
   ignore case and repeated whitespace. Deadlines compare by their UTC instant.
   Two unknown deadlines count as the same deadline for this rule.

The second rule also catches the same assignment imported once manually and once
with an external ID. Duplicate create/update raises `DuplicateAssignmentError`;
it never silently merges or overwrites records. Distinct tasks with identical
course/title/deadline require a distinguishing title. A source message containing
multiple assignments will need a per-assignment external identifier in a future
integration. If a title or deadline changes and there is no stable external ID,
a human must reconcile the records; this phase does not perform fuzzy matching.

### Synthetic example in a temporary database

Run from the project root with the virtual environment activated:

```python
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from app.database import Database
from app.models import Assignment, Status

with TemporaryDirectory() as directory:
    database = Database(Path(directory) / "assignments.db")
    record = database.create_assignment(Assignment(
        course="Synthetic CS",
        title="Practice task",
        deadline=datetime(2030, 12, 1, 23, 59, tzinfo=ZoneInfo("Asia/Karachi")),
    ))
    database.update_assignment(record.id, status=Status.REVIEWED)
    print(database.get_assignment(record.id))
```

## Configuration

Defaults require no configuration. `ASSIGNMENT_DATA_DIR` and `ASSIGNMENT_LOG_DIR`
control storage locations. The database is always named `assignments.db` within
the configured data directory. Relative paths resolve against the project root.
Empty values are rejected. Local reminder commands need no credentials; Gmail
uses a private OAuth client/token directory documented in the Gmail setup guide.

The application reads exported environment variables; it does not automatically
load `.env`. To use the example with Bash:

```bash
cp .env.example .env
# Edit your own .env first; only source files you trust.
set -a
source .env
set +a
```

Do not commit secrets, tokens, downloaded instructions, or personal email data.
`.gitignore` excludes common credential files and local data, but provides no
encryption. SQLite storage is a local unencrypted file.

## Project layout

- `app/main.py`: command-line interface.
- `app/config.py`: environment configuration and local paths.
- `app/models.py`: immutable validated records, statuses, and timezone helpers.
- `app/database.py`: SQLite initialization and repository operations.
- `app/notifier.py`: notification protocol and bounded `notify-send` adapter.
- `app/scheduler.py`: one-shot reminder checker and daily summary grouping.
- `app/gmail_monitor.py`: manual Gmail scanning and new-assignment notifications.
- `app/gmail_discovery.py`: broad multi-query ID discovery and bounded recent-mail fallback.
- `app/gmail_auth.py`: read-only desktop OAuth, token refresh, and safe API errors.
- `app/assignment_parser.py`: plain-text/HTML email parsing and conservative extraction.
- `app/assignment_classifier.py`: weighted assignment evidence with readable reasons.
- `docs/GMAIL_SETUP.md`: exact Google Cloud and local authorization steps.
- `deploy/systemd/`: reviewable user services/timers; not automatically installed.
- `examples/reminder_demo.py`: synthetic demonstration with mocked notifications.
- `data/`, `logs/`: ignored runtime storage with tracked `.gitkeep` files.
- `tests/`: import, model, database, and CLI tests.

## Validation

```bash
source .venv/bin/activate
python -m compileall app
pytest
```

Every test receives temporary data/log environment paths through an automatic
fixture. Database tests also explicitly use pytest's `tmp_path`. Tests use only
synthetic records and never use the normal `data/assignments.db`.

## Academic integrity and scope

Gmail OAuth and Bahria LMS browser-based assignment scanning are implemented.
Actual submission is not implemented. Existing user timers run local reminders,
daily summaries and the current-course LMS scan. Gmail is not scheduled.

Future features may detect assignments, download instructions, summarize
requirements, extract deadlines, organize approved files, and create reminders.
They must not generate graded assignment answers and submit them as the student's
work. Every actual final submission must require explicit student approval.

Recommended next step: complete [manual Gmail setup](docs/GMAIL_SETUP.md), authorize
your own Desktop OAuth client, and verify a manual Gmail scan before scheduling it.


## Phase 3: local reminders

```bash
source .venv/bin/activate
python -m app.main test-notification
python -m app.main check-reminders
python -m app.main daily-summary
python -m app.main daily-summary --notify
python -m examples.reminder_demo
```

`test-notification` sends a harmless message without opening the assignment
database. `check-reminders` performs one check and exits. `daily-summary` prints
to the terminal; `--notify` additionally sends the summary to the desktop. A
manual summary is intentionally repeatable; it does not consume reminder events.
The example uses a temporary database, a fixed Karachi clock, and mock
notifications, and deletes its temporary database afterward.

### Calendar-day reminders and duplicate prevention

Each unsubmitted assignment with a known deadline gets only two reminder types,
based on calendar dates in **Asia/Karachi**:

| Eligible date | Reminder type | Notification |
| --- | --- | --- |
| The calendar day before the deadline | `DUE_TOMORROW` | Assignment Due Tomorrow |
| The deadline's calendar date | `DUE_TODAY` | Assignment Due Today (critical urgency) |

For a deadline of September 20, 2026 at 11:59 PM, reminders are eligible on
September 19 and September 20. The first successful five-minute check on each
eligible date sends that day's notification. It does not wait until exactly
24 hours before the deadline. Later checks on the same date remain silent.
If the computer was off, a check later on that date can still deliver the alert;
missed prior dates are not replayed. A deadline earlier today is still eligible
for Due Today until midnight. No reminder is sent on dates after the due date.

Example bodies: `AI Assignment 2 is due tomorrow at 11:59 PM.` and
`AI Assignment 2 is due today at 11:59 PM.` The due-day alert uses critical
urgency. Submitted assignments and unknown deadlines are always skipped.

There are no separate three-day, six-hour, one-hour, or overdue notifications.
Overdue status tracking and the daily summary (including its three-day grouping)
remain unchanged. The checker still runs every five minutes and the daily summary
still runs at 8 PM; checking frequency does not imply repeated notifications.

Initialization transactionally migrates existing reminder tracking without
changing assignment records. Its active `reminder_events` table contains:

- `assignment_id`: foreign key to assignments, deleted automatically with its parent.
- `deadline`: canonical UTC deadline identifying this version of the deadline.
- `reminder_type`: `DUE_TOMORROW` or `DUE_TODAY`, enforced by SQLite.
- `sent_at`: timezone-aware UTC timestamp of the successful checker event.

On migration, old delivery rows are retained in `reminder_events_legacy` for
history only. Any prior delivery on the day before or the due date is mapped to
the corresponding new type, based on its Karachi delivery date, to avoid another
alert on the same eligible date after upgrading. Old types cannot be inserted
into the active table or sent by the repository API. Migration is idempotent.

The composite primary key is `(assignment_id, deadline, reminder_type)`.
Deadline edits allow fresh reminders for the new deadline and retain earlier
history. Restoring an old deadline reuses that deadline's existing history.
Reopening a submitted assignment does not erase already delivered reminders.

A SQLite write transaction serializes the duplicate check, bounded notification
call, and event insertion, including across concurrent checker processes. The
assignment is reread under the lock to skip deleted, submitted, or changed-deadline
records. Notification failure rolls back its event, reports an error, and leaves
it eligible for retry on the same eligible date; other assignments continue. Successful events survive
restarts. Foreign keys are enabled on every repository connection.

Desktop delivery and an SQLite commit cannot form a single atomic operation.
If the process crashes after the desktop accepts a message but before the event
commits, a subsequent retry can duplicate that message. Successful `notify-send`
means the desktop accepted the request, not that the user saw it: Do Not Disturb,
notification settings, or desktop truncation can affect display. The full summary
remains available in the terminal or systemd journal.

### Daily summary grouping

The summary lists New Assignments, Due Tomorrow, Due Within 3 Days, Overdue, and
Not Submitted, printing `None` for empty groups. Tomorrow means the next calendar
date in Asia/Karachi. Within 3 Days means the inclusive interval from now to
72 hours from now. Groups intentionally overlap; tomorrow's tasks also appear
within 3 days and in Not Submitted. Submitted records are excluded throughout.
Unknown deadlines still appear in New/Not Submitted. Overdue is calculated from
the deadline, even if a reminder check has not yet updated the stored status.
The summary itself does not change assignment statuses.

### Ubuntu notification requirements

`notify-send` is provided by Ubuntu's `libnotify-bin` package. If it is missing,
the app reports a clear error; it never silently marks a failed reminder sent.
To install it yourself if needed:

```bash
sudo apt install libnotify-bin
```

Run the app as your normal logged-in desktop user. Notifications need the user's
D-Bus session and notification daemon. No GUI test notification is sent by pytest;
notification functions/subprocesses are mocked, and an automatic fixture blocks
unmocked subprocess calls. Every test uses temporary database/log paths.

## Optional systemd user schedule (approval required)

These files have been generated inside the project only:

- `deploy/systemd/assignment-agent-summary.service`
- `deploy/systemd/assignment-agent-summary.timer`
- `deploy/systemd/assignment-agent-reminders.service` (optional periodic checks)
- `deploy/systemd/assignment-agent-reminders.timer` (optional periodic checks)

The summary service runs the project's `.venv/bin/python -m app.main daily-summary
--notify` from `%h/Desktop/assignment-agent`. The timer uses exactly:

```ini
OnCalendar=*-*-* 20:00:00 Asia/Karachi
Persistent=true
AccuracySec=1min
```

`%h` is your home directory; `%t` is your user runtime directory. The services
set `DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus` and use `UMask=0077`. They use
project-default storage paths. They do not source `.env`; for custom data paths,
add explicit `Environment=ASSIGNMENT_DATA_DIR=/absolute/path` and
`Environment=ASSIGNMENT_LOG_DIR=/absolute/path` lines to the service files before
installation. If the project moves, update both services' paths.

This is a user schedule, not a root/system service. It relies on the user manager
and an available desktop session. `Persistent=true` catches a missed daily event
when the timer is next activated (possibly at login, outside 8 PM). It does not
wake a powered-off machine. An unavailable notification daemon causes a visible
service failure; the summary remains in the journal. The five-minute checker
retries failed deadline notifications on its next run. Daily summary delivery is
attempted once per daily service invocation. No user lingering is enabled.

**Do not run these installation commands until you choose to enable the schedule.**
They copy precisely the two summary files into your user systemd directory:

```bash
cd ~/Desktop/assignment-agent
mkdir -p ~/.config/systemd/user
install -m 644 deploy/systemd/assignment-agent-summary.service ~/.config/systemd/user/
install -m 644 deploy/systemd/assignment-agent-summary.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now assignment-agent-summary.timer
```

The daily summary timer does not run threshold reminders. To additionally enable
the optional checker (first check about two minutes after user-manager startup,
then every five minutes), separately approve and run:

```bash
install -m 644 deploy/systemd/assignment-agent-reminders.service ~/.config/systemd/user/
install -m 644 deploy/systemd/assignment-agent-reminders.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now assignment-agent-reminders.timer
```

Inspect the schedule and logs:

```bash
systemctl --user list-timers 'assignment-agent-*'
journalctl --user -u assignment-agent-summary.service -n 50
journalctl --user -u assignment-agent-reminders.service -n 50
```

To stop future executions:

```bash
systemctl --user disable --now assignment-agent-summary.timer
systemctl --user disable --now assignment-agent-reminders.timer
```

The generated unit files can be validated without installing or enabling them:

```bash
systemd-analyze --user verify deploy/systemd/assignment-agent-*.service deploy/systemd/assignment-agent-*.timer
systemd-analyze calendar '*-*-* 20:00:00 Asia/Karachi'
```


## Phase 4: Gmail commands

```bash
python -m app.main gmail-auth
python -m app.main gmail-status
python -m app.main gmail-scan
```

Only `gmail-auth` opens Google's consent page. `gmail-status` verifies saved access
without displaying tokens. `gmail-scan` reads recent emails, creates reviewable
assignments, and notifies only for new records. `gmail-auth --force` replaces
expired/revoked authorization through fresh consent. Required OAuth scopes are
`https://www.googleapis.com/auth/gmail.readonly` and
`https://www.googleapis.com/auth/gmail.send`. Existing tokens require explicit
`python -m app.main gmail-auth --force` reauthorization.

Configure the score threshold, trusted senders, candidate keywords, optional sender
filters, and scan window through exported
variables in `.env.example`. The default window is seven days, capped at 100
candidate IDs per scan with a visible truncation warning. Credential files live at
`credentials/gmail_credentials.json` and `credentials/gmail_token.json`; the
whole directory is ignored by Git. No real credentials are bundled.

[The Gmail guide](docs/GMAIL_SETUP.md) covers Google Cloud setup, permissions,
parsing limits, duplicate tracking, and retry behavior. Tests use temporary
storage, synthetic email, and mocked API/OAuth/notification calls. Real network
and browser access are blocked by automatic test fixtures.


### Review Gmail classifications before importing

```bash
python -m app.main gmail-scan --dry-run --verbose
```

This evaluates recent search-matching candidates (including processed IDs) without opening SQLite or
sending notifications. It shows candidate subject, sender, classification, score,
and reasons, not message bodies or credentials. Normal scans still deduplicate
processed IDs. Existing records are not automatically removed or reclassified.

Strong assignment-specific evidence is mandatory. Generic due dates, notes,
projects, submissions, and course references do not suffice. The configurable
`ASSIGNMENT_SCORE_THRESHOLD` defaults to 6; unknown senders require 8. Optional
`TRUSTED_ACADEMIC_SENDERS` and `TRUSTED_ACADEMIC_DOMAINS` add confidence but never
bypass that evidence requirement. Financial and marketing language reduces the
score. Full weights and examples are in [the Gmail guide](docs/GMAIL_SETUP.md).


### Gmail API efficiency

Scans search assignment-specific and broader academic terms before fetching metadata, then retrieve
full bodies only for relevant subject/sender/snippet candidates. Defaults:

```bash
GMAIL_SCAN_DAYS=7
GMAIL_MAX_MESSAGES=100
GMAIL_FETCH_DELAY_MS=100
GMAIL_MAX_RETRIES=4
GMAIL_UNREAD_ONLY=false
```

`GMAIL_SEARCH_TERMS` optionally changes the search terms. The classifier and
`gmail.readonly` scope are unchanged. Metadata gating can miss unknown senders
whose assignment evidence appears only deep in the body; configure trusted
academic senders and validate the dry-run before importing.

Rate limits and temporary server errors use bounded exponential backoff with
jitter and Retry-After support. The CLI reports candidate IDs, full fetches,
classifications, rejections, skips, retries, rate-limit failures, and other errors.
See [the Gmail guide](docs/GMAIL_SETUP.md) for the exact retry and counting rules.


### Diagnose empty Gmail discovery

```bash
python -m app.main gmail-scan --dry-run --verbose --discovery-debug
```

Discovery uses two queries: assignment-specific terms, then broader deadline,
due, submission, LMS, course, and class terms. IDs are merged under a single
100-message limit. If both queries return no IDs, metadata for up to 25 recent
messages is inspected as a fallback. Set `GMAIL_DISCOVERY_TERMS` to customize the
broad terms and `GMAIL_FALLBACK_MESSAGES=0` to disable fallback (default 25, max 50).

The debug flag shows query counts, unique IDs, fallback usage, and metadata
candidates. Full bodies are still fetched only for relevant metadata candidates.
The strict classifier and thresholds are unchanged; broader discovery is not
permission to create assignments from generic deadlines. Dry-run performs no
SQLite access or notifications. Existing user timers remain unchanged.

### Bahria LMS — Stage 1 manual browser sessions

Install browser support inside the existing virtual environment:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m app.main lms-auth
python -m app.main lms-status
python -m app.main lms-inspect
```

On first authentication, paste the exact HTTPS portal URL you normally use.
Alternatively export `BAHRIA_PORTAL_URL` (the app does not automatically load `.env`).
Log in in the visible Chromium window, complete any CAPTCHA/MFA yourself, and type
`yes` in the terminal only after your authenticated portal is visible. The app never
asks for your password. It saves storage state to
`credentials/bahria_storage_state.json` and the supplied portal URL to
`credentials/bahria_portal.json`; both are ignored by Git and written with mode 0600.
Treat browser state like a password: it contains authentication cookies/storage.
Playwright storage state does not preserve sessionStorage; a portal relying on it
may require manual login again. No password input fields or browser profile are saved.

`lms-status` checks the saved state by opening the configured portal headlessly.
A visible password field or login redirect requires `lms-auth` again. An off-origin
redirect is unverified. “Appears usable” is deliberately provisional until the real
portal's authenticated markers have been inspected; it is not proof of authentication.
Network/browser failures are reported without printing raw browser diagnostics.

`lms-inspect` opens a visible browser using saved state. Navigate to the assignment page, then press Enter in the terminal. If the LMS
opens a new tab, select its number from the sanitized tab list. Only bounded visible structural labels are printed;
input values, table rows, cookies, full DOM, and URL paths/queries/fragments are omitted.
Common sensitive labels, emails, and identifiers are redacted. Names can occur in
arbitrary labels, so inspect an assignment/navigation page, not a profile page, and
review output before sharing. A different HTTPS origin requires explicit confirmation that the selected tab is
your authenticated Bahria LMS page. This permits inspection once and does not
change the configured portal or saved authentication state.
These commands never open SQLite, import assignments, send assignment notifications,
or install background timers. Assignment scanning is described below.
Browser security/TLS checks remain enabled. If Chromium reports missing system
libraries or unavailable sandbox support, fix the Ubuntu host setup rather than
turning off browser security.

On Ubuntu with restricted unprivileged user namespaces, the adapter can use the
already-installed `/opt/google/chrome/chrome-sandbox` helper when it is root-owned,
setuid, and not writable by group/others. `CHROME_DEVEL_SANDBOX` is supplied only to
the child browser process. No shell profile, AppArmor policy, or system setting is
changed, and Chromium sandboxing stays enabled.

### Current-course LMS scanning

`python -m app.main lms-courses --dry-run --verbose` discovers current courses.
`python -m app.main lms-scan --dry-run --verbose` reads each current course's
Assignments table. See the [multi-course guide](docs/LMS_MULTICOURSE.md) for filters,
scope and limitations. Use `--single-course` for the original interactive flow. `python -m app.main lms-scan`
imports locally and notifies only for new records. See [Bahria scanning notes](docs/BAHRIA_LMS.md)
for matching rules, read-only dry-run behavior, and observed deadline/submission
evidence from two populated rows. Detail scraping and Action navigation remain
unimplemented because no assignment detail link has been verified.

### Optional Bahria LMS user timer

The verified LMS scanner now supports a private, explicitly configured course
page in headless mode. A 30-minute systemd user service/timer is staged in
`deploy/systemd/`; it is not installed or enabled automatically. See
[LMS automation setup and verification](docs/LMS_AUTOMATION.md) for exact units,
installation commands, authentication-expiry cooldown, journal logs and coverage.
Gmail, deadline reminder schedules and the 8 PM summary remain unchanged.

### Gmail alerts on your phone

Optional `ALERT_EMAIL` enables email for new unsubmitted LMS assignments, the
existing due-tomorrow/due-today reminder events, and LMS authentication expiry.
Blank disables email. Desktop delivery remains independent. See
[email notification setup and duplicate protection](docs/EMAIL_NOTIFICATIONS.md)
for reauthorization, the manual `test-email` command, and scheduled configuration.

### Email a fresh current-semester report

`python -m app.main email-assignment-report` scans every current registered LMS
course, updates local records, and sends one report to exported `ALERT_EMAIL`.
See [report usage](docs/EMAIL_NOTIFICATIONS.md#manual-current-semester-assignment-report)
for loading the private configuration and coverage/failure behavior. Reports are
manual only; this command does not dispatch separate new-assignment alerts.
