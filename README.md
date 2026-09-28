# Assignment Agent

Assignment Agent is a Python application for monitoring Bahria University LMS
assignments, maintaining local SQLite records, and sending desktop and Gmail
notifications. It supports multiple registered students with separate browser
sessions, current-course scanning, deadline tracking, and daily summaries.

Students sign in manually through Chromium. The application reads assignment
information; it does not upload coursework or submit assignments to the LMS.

## Installation

Use a Linux environment with Python and a virtual environment compatible with
[requirements.txt](requirements.txt). Interactive LMS authentication requires a
graphical desktop. Run these commands from your checkout:

```bash
cd /path/to/assignment-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m app.main --help
```

Chromium requires its Linux system dependencies and working sandbox support.
Resolve host dependency errors before authenticating; browser sandboxing and TLS
verification remain enabled. Desktop notifications use `notify-send`, provided
on Ubuntu by `libnotify-bin`:

```bash
sudo apt install libnotify-bin
```

Local commands initialize the SQLite database as needed. No credentials or
student records are bundled. Register a student before importing that student's
assignments. Existing deployments should back up private data and review the
applicable migration source in `scripts/` before upgrading; do not run every
migration script indiscriminately.

## Configuration and private storage

The application reads exported environment variables and does not automatically
load `.env`. To use a trusted local configuration file:

```bash
cp .env.example .env
# Edit .env for your installation before sourcing it.
set -a
source .env
set +a
```

| Setting or location | Purpose |
| --- | --- |
| `ASSIGNMENT_DATA_DIR` | Database and scan notices; defaults to `data/` |
| `ASSIGNMENT_LOG_DIR` | Log directory; defaults to `logs/` |
| `GMAIL_CREDENTIALS_DIR` | Gmail OAuth client and token directory; defaults to `credentials/` |
| `ALERT_EMAIL` | Recipient for manual test email and legacy assignment reports |
| `credentials/students/<student-id>/` | Private per-student portal configuration, snapshot, and Chromium profile |

Relative configured directories resolve against the project root. The database
is named `assignments.db`. Student authentication paths remain under the project's
`credentials/students/` directory; changing `ASSIGNMENT_DATA_DIR` does not move
those profiles. Student event emails use the registered student's email address.

Keep `.env`, OAuth files, browser profiles, snapshots, databases, logs, and backups
private. Git ignore rules prevent common accidental additions; they do not encrypt
files or protect data from other processes running as the same OS user.

## Register, authenticate, and scan students

The examples use `student-example` as a generic student ID. Replace it with your
own ID. IDs allow lowercase letters, digits, and single separating hyphens.
Registration prompts for the student's name and notification email:

```bash
python -m app.main student-add --id student-example
python -m app.main student-list
python -m app.main student-auth student-example
python -m app.main student-scan student-example --dry-run
python -m app.main student-scan student-example
python -m app.main scan-all-students --dry-run
python -m app.main scan-all-students
```

At first authentication, provide the HTTPS portal address when prompted. The
student enters their credentials and completes any MFA/CAPTCHA directly in
Chromium. Confirm the authenticated portal, then follow the terminal instructions
to open **Go To LMS**, a current course, and its **Assignments** page. Select and
confirm the LMS tab when requested.

Each student reuses a dedicated persistent Chromium profile. Existing private
`storage_state` snapshots bootstrap profiles and remain as compatibility snapshots.
Authentication and scans share a per-student lock. A busy or expired student does
not prevent the all-student command from attempting the other enabled students.

On detected expiry, the scanner can attempt one supported CMS-to-LMS recovery.
If authentication is still required, a normal scan marks that student
`LOGIN_REQUIRED`, attempts a login notice, and applies a retry cooldown. Successful
`student-auth` clears the scan cooldown and marks the student `ACTIVE`.

Dry-run skips assignment reconciliation writes, authentication-status updates,
cooldown updates, and notifications. CLI database initialization can still occur,
and Chromium may create or refresh private browser state. It is not a filesystem
snapshot or a guarantee that no files change.

See [Student browser sessions](docs/STUDENT_SESSIONS.md) for the lifecycle,
notification behavior, security boundaries, and recovery limitations. CLI output
such as `student-list` contains personal information; review it before sharing.

## Assignment CLI and storage

```bash
python -m app.main assignments
python -m app.main new
python -m app.main upcoming
python -m app.main overdue
python -m app.main details 1
python -m app.main mark-submitted 1
python -m app.main delete 1
python -m app.main check-reminders
python -m app.main daily-summary
python -m app.main daily-summary --notify
python -m app.main daily-summary-all
python -m app.main daily-summary-all --notify
```

- `assignments`, `new`, and `upcoming` list records; `new` does not create a record.
- `overdue` marks eligible past-deadline records overdue before listing them.
- `details ID` displays a local record. Replace example IDs with an existing ID.
- `mark-submitted ID` records a submission already completed by the student; it
  does not perform an external submission.
- `delete ID` asks for confirmation; `delete ID --yes` skips that prompt. Deletion
  affects local records and related local history, not Gmail or LMS content.
- `daily-summary-all` produces separate summaries for enabled students. Ordinary
  assignment listing commands are operator views and do not accept a student filter.

Records include student ownership, course, title, deadline, source, local status,
and review metadata. Statuses include `NEW`, `REVIEWED`, `IN_PROGRESS`,
`READY_TO_SUBMIT`, `SUBMITTED`, and `OVERDUE`. Unknown deadlines remain reviewable.
Datetimes must be timezone-aware; storage uses UTC and display/reminder calendar
rules use `Asia/Karachi`.

SQLite uniqueness checks are scoped to the student: external identifiers are
unique within a source, and normalized course/title/deadline keys also prevent
duplicates. LMS reconciliation can enrich existing records and track deadline
changes. Review ambiguous or missing information rather than treating extraction
as authoritative. The application stores local records unencrypted.

## Gmail setup and discovery

Follow [Gmail setup](docs/GMAIL_SETUP.md) to configure a Google Cloud OAuth client.
Authorize the read and send scopes required by the current application:

```bash
python -m app.main gmail-auth
python -m app.main gmail-status
python -m app.main gmail-scan --dry-run --verbose
python -m app.main gmail-scan
```

Use `gmail-auth --force` when fresh consent is required. Tokens and OAuth client
files remain in the private credentials directory. Gmail discovery is a separate
legacy workflow; it does not authenticate or scan every registered LMS student.

Dry-run classifies candidates without opening SQLite or sending notifications.
Verbose output can contain email subjects and sender addresses. For discovery
counts and query diagnostics:

```bash
python -m app.main gmail-scan --dry-run --verbose --discovery-debug
```

Defaults scan seven days with a limit of 100 candidate IDs. Relevant settings
include `GMAIL_SCAN_DAYS`, `GMAIL_MAX_MESSAGES`, `GMAIL_FETCH_DELAY_MS`,
`GMAIL_MAX_RETRIES`, `GMAIL_SEARCH_TERMS`, `GMAIL_DISCOVERY_TERMS`, and
`GMAIL_FALLBACK_MESSAGES`. Assignment-specific evidence is required; trusted
senders do not bypass classification. API retries are bounded. The Gmail guide
explains filters, thresholds, deduplication, and discovery limitations.

## Notifications and summaries

Desktop and Gmail delivery are independent. Desktop deadline reminders use
Karachi calendar dates: the day before the deadline and the deadline date itself.
Submitted assignments and unknown deadlines are skipped. Delivery history
suppresses repeated successful reminders for the same deadline and type.

The email pipeline supports new assignments, due-tomorrow/due-today reminders,
overdue notices, deadline extensions/shortenings, and authentication-expiry notices.
Student event recipients come from the enabled student registry. Gmail delivery
requires usable OAuth authorization and the expected notification schema.
Setting `ALERT_EMAIL` is not a global on/off switch for student event emails.

```bash
python -m app.main test-notification
python -m app.main test-email
python -m app.main email-assignment-report
```

These commands send real notifications when configured. The manual assignment
report uses the legacy LMS configuration and `ALERT_EMAIL`; it is not an
all-student report. See [email notifications](docs/EMAIL_NOTIFICATIONS.md) for
report details and authorization setup; student session behavior is documented
in [the session guide](docs/STUDENT_SESSIONS.md).

Summaries group new, due-tomorrow, due-within-three-days, overdue, and unsubmitted
records. Groups overlap. A manual summary is repeatable and does not consume
reminder events. Desktop acceptance does not guarantee that the user saw an
alert; desktop settings and Do Not Disturb can suppress display. A crash between
external delivery and recording success can still cause a duplicate on retry.

## Legacy LMS commands

The `lms-*` commands use the legacy single-user configuration and JSON snapshot,
not a selected student's persistent profile:

```bash
python -m app.main lms-auth
python -m app.main lms-status
python -m app.main lms-inspect
python -m app.main lms-scan --configure-background
python -m app.main lms-courses --dry-run --verbose
python -m app.main lms-scan --dry-run --verbose
python -m app.main lms-scan
```

`lms-status` provides a conservative portal check, not proof of access to every
course. `lms-inspect` displays bounded structural information; inspect an assignment
page and review output before sharing it. `--configure-background` verifies and
saves an LMS target; it does not install a scheduler and cannot be combined with
`--dry-run`. `lms-scan --single-course` retains the interactive single-course flow.

See [Bahria LMS notes](docs/BAHRIA_LMS.md),
[multi-course scanning](docs/LMS_MULTICOURSE.md), and
[LMS automation](docs/LMS_AUTOMATION.md) for legacy navigation and deployment guidance.

## Operations and scheduling

CLI scans and reminder checks run once and exit. The repository provides systemd
unit sources in `deploy/systemd/` and `deploy/vps/systemd/`; cloning or running a
scan does not install or enable them.

Before installing units, review their working directory, interpreter path,
environment, schedule, and target command. The supplied LMS units invoke legacy
`lms-scan`. A multi-student deployment must deliberately configure
`python -m app.main scan-all-students` with the same project, OS account, database,
and private profiles used for authentication. Shell `.env` files are not loaded
by systemd automatically. Use explicit service configuration for required settings.

Desktop notifications require the logged-in user's D-Bus session. Headless scans
still need Chromium and its dependencies; interactive reauthentication needs a
display. A timer cannot extend a server-expired Bahria session.

Useful read-only operational checks include:

```bash
systemctl --user list-timers 'assignment-agent-*'
journalctl --user -u assignment-agent-lms-scan.service -n 50
journalctl --user -u assignment-agent-reminders.service -n 50
journalctl --user -u assignment-agent-summary.service -n 50
```

Use the unit names configured for your deployment. Consult
[LMS automation](docs/LMS_AUTOMATION.md) and [VPS deployment](docs/VPS_DEPLOYMENT.md)
before installing or changing a schedule. Back up private storage securely and
avoid concurrent copying of a live database or Chromium profile. Do not remove
browser locks to bypass an active scan.

## Project layout

| Path | Responsibility |
| --- | --- |
| `app/main.py` | CLI and student workflows |
| `app/students.py` | Student registry and session-path validation |
| `app/lms/` | Browser sessions, course discovery, extraction, reconciliation |
| `app/database.py`, `app/models.py` | SQLite storage and validated records |
| `app/gmail_*.py` | Gmail OAuth, discovery, reading, and retries |
| `app/email_notifications.py`, `app/scheduler.py` | Notifications and summaries |
| `app/web/` | FastAPI dashboard and API implementation |
| `deploy/` | Reviewable systemd deployment sources |
| `docs/`, `tests/` | Technical guides and automated coverage |

## Validation

```bash
python -m compileall -q app
python -m pytest -q tests/test_student_sessions.py
python -m pytest -q
```

Tests use temporary storage, synthetic data, and mocked network/notification
access. The session tests model the notification audit schema with fixture-level
migrations; they do not by themselves prove fresh-install migration completeness.
Review actual test output rather than assuming every suite passes. Automated
session tests do not establish Bahria's live session lifetime or actual email
delivery. No private runtime fixtures belong in the repository.
