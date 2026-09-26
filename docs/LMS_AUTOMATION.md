# Bahria LMS user timer (staged, not enabled)

The timer runs every 30 minutes, at minutes 00 and 30. `Persistent=true` makes
systemd run one catch-up scan when an elapsed calendar trigger was missed while
the user manager was stopped. This is a user service: no sudo or system service,
and no changes to Gmail, reminders, or the daily summary.

## Private setup and scope

First run the existing manual import and inspect its results:

```bash
cd ~/Desktop/assignment-agent
source .venv/bin/activate
python -m app.main lms-scan
python -m app.main assignments
```

Configure unattended access once:

```bash
python -m app.main lms-scan --configure-background
```

Navigate through CMS to the desired course Assignments page. The command verifies
the session on the selected table and saves refreshed private Playwright state.
It then verifies that the selected URL can be reopened with the same course and
semester before saving `credentials/bahria_scan_target.json`. If replay fails,
the verified session is retained but an existing target is not replaced.
Both files have mode 0600 and are ignored by Git. No timer is installed or enabled
by configuration. Configuration also performs a normal deduplicated scan.

**Coverage is the configured course and current page only.** This does not claim
all-course or pagination support. A URL that cannot reproduce the selected course
is rejected; the scanner never guesses an assignment Action URL or submits a form.
The service uses the environment flag `ASSIGNMENT_LMS_BACKGROUND=1` to make the
unchanged `python -m app.main lms-scan` command noninteractive and headless. It never
clicks assignment actions or enters login credentials.

A manual unattended validation can be run before installing:

```bash
ASSIGNMENT_LMS_BACKGROUND=1 python -m app.main lms-scan --dry-run
ASSIGNMENT_LMS_BACKGROUND=1 python -m app.main lms-scan
```

## Authentication and notifications

A missing or expired session exits successfully without an import. It sends
"Bahria LMS login required" with the instruction to run
`python -m app.main lms-auth`. A private state file at
`data/lms-auth-notice.json` suppresses repeated notices and network attempts for
24 hours. A changed session file bypasses the network cooldown immediately; a
successfully authenticated, validated course table resets the notification state.
Notification delivery failure also backs off instead of retrying every half hour.

When a background target exists, `lms-auth` asks you to open that course's LMS
Assignments page before saving, so both CMS and LMS session cookies are refreshed.
It verifies replay before replacing the private target. No automatic credential
entry or repeated login attempts occur. HTTP/network/DOM errors are logged safely
and fail the service once; there is no systemd automatic restart loop.

Background scans hold a nonblocking file lock to avoid overlapping browser runs.
The existing transactional storage deduplicates assignments, updates verified
metadata, and only queues notifications for genuinely new unsubmitted records.
An unchanged scan logs zero updated records; observation timestamps do not count
as assignment changes. Ambiguous changed fallback identities still require manual
review rather than creating a duplicate. Existing submission, deadline conflict,
and reminder semantics are unchanged.

## Proposed installation (requires explicit approval)

Destination: `~/.config/systemd/user/`.
The exact source units are in `deploy/systemd/assignment-agent-lms-scan.service`
and `deploy/systemd/assignment-agent-lms-scan.timer`. `%h` expands to your home
folder; `%t` expands to the user runtime folder containing the desktop D-Bus socket.
No credential values occur in either unit.

Review the files and validate them before approval:

```bash
systemd-analyze --user verify deploy/systemd/assignment-agent-lms-scan.service deploy/systemd/assignment-agent-lms-scan.timer
systemd-analyze calendar --iterations=3 '*-*-* *:00,30:00'
```

Only after explicit approval:

```bash
install -d -m 700 ~/.config/systemd/user
install -m 644 deploy/systemd/assignment-agent-lms-scan.service deploy/systemd/assignment-agent-lms-scan.timer ~/.config/systemd/user/
systemd-analyze --user verify ~/.config/systemd/user/assignment-agent-lms-scan.service ~/.config/systemd/user/assignment-agent-lms-scan.timer
systemctl --user daemon-reload
systemctl --user enable --now assignment-agent-lms-scan.timer
systemctl --user status assignment-agent-lms-scan.timer --no-pager
systemctl --user list-timers assignment-agent-lms-scan.timer --all --no-pager
```

The timer may perform a catch-up scan immediately after activation. It runs only
while the user's systemd manager is running; enabling linger is not part of setup.

## Logs and stopping

```bash
journalctl --user -u assignment-agent-lms-scan.service --since today --no-pager
systemctl --user disable --now assignment-agent-lms-scan.timer
```

The journal records scan start, authentication outcome, parsed/new/existing/updated/
review/notification/error counts, and completion. Error text from Playwright, URLs,
cookies, passwords, OAuth tokens, storage state and credential contents are never
logged. Do not run background scans with browser debugging enabled. The oneshot
service has a three-minute timeout and does not restart automatically.

## Verified checkpoint: 20 September 2026

- First real interactive import: 2 parsed, 2 new, 0 existing, 0 updated, 1 needs
  review, 1 notification, 0 errors. The already-submitted Lab 02 did not notify.
- Stored records: ID 1, Lab 01, `OVERDUE`, actual deadline September 9 at 09:00
  Asia/Karachi, needs review for conditional-extension applicability; ID 2, Lab 02,
  `SUBMITTED`, deadline September 16 at 09:00 Asia/Karachi, no review required.
  There are zero duplicate course/title groups. Both Lab 01 raw timestamps remain
  in LMS metadata.
- Private target setup verified replay of Introduction to Data Science Lab. Its
  repeat import found 2 existing, 0 new, 0 updated, 0 notifications, and 0 errors.
- A real headless run using `ASSIGNMENT_LMS_BACKGROUND=1` authenticated successfully
  and again found 2 existing, 0 new, 0 updated, 1 needs review, 0 notifications,
  and 0 errors. It exited 0 without any interactive input.
- Compilation succeeded and all 326 tests passed. `systemd-analyze --user verify`
  exited 0 for both staged units. Calendar validation confirmed half-hour triggers.
- Neither unit has been installed or enabled. Installation awaits explicit approval.
