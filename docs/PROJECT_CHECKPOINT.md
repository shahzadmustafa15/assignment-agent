# Durable project checkpoint — 20 September 2026

Read this file when resuming work in `~/assignment-agent`.
This is the existing project. Do not recreate completed phases. Use `.venv/bin/python`.
No passwords, cookies, tokens, or private session contents belong in this checkpoint.

## Working baseline to preserve

- SQLite assignment storage, Ubuntu desktop notifications, and deduplication work.
- Deadline reminders: one day before and on deadline day only. Existing reminder
  timer and daily 8 PM summary are enabled. Do not change these policies.
- Gmail OAuth uses gmail.readonly. Strict classification, discovery, dry-run,
  debugging, and bounded retry work. Gmail integration was not modified here.
- Playwright 1.63.0 and Chromium are installed. CMS → LMS login and private session
  storage work. User manually enters credentials; never automate credential entry.
- Existing LMS assignment parser preserves raw deadline evidence, submission-file
  evidence, marks, returned comments, and action state. Do not weaken these rules.

## Verified assignment semantics and currently stored records

Course: Introduction to Data Science Lab.

1. Database ID 1: Lab 01 Tasks: Introduction to Python IDEs and Basics.
   Actual deadline: 2026-09-09 09:00 Asia/Karachi. OVERDUE, needs_review=true.
   Both visible raw timestamps remain preserved:
   `9 September 2026-09:00 am`, labeled `title="Actual"`, and
   `9 September 2026 - 11:55 pm`, labeled `title="Extended only for missing"`.
   Both are visible `small` elements in the Deadline cell, not hidden duplicates.
   The explicit Actual label identifies the main deadline. Conditional extension
   applicability remains unresolved; deadline_conflict=true stays in metadata.
2. Database ID 2: Lab 02: Python Loops, Data Structures, Functions & Modules.
   Deadline: 2026-09-16 09:00 Asia/Karachi. SUBMITTED, needs_review=false.
   A verified AssignmentSubmissions file link proves submission; plain Submission
   text alone never does. No Submission means not submitted.

Each is stored exactly once; there were zero duplicate course/title groups.
First real import: 2 new, 1 notification (Lab 01), 0 errors. Repeated interactive and
headless scans: 2 existing, 0 new, 0 updated, 0 notifications, 0 errors.
No verified stable LMS assignment ID or assignment detail URL was found. Known
fallback IDs hash course/title/confident deadline. Unresolved ones hash normalized
course/title/assignment number/semester, excluding ambiguous timestamps. A lone
conditional extension cannot establish the actual deadline. Clearing review also
clears stored review reasons. Submitted records do not generate new notifications.

## Installed user systemd units — already approved and active

The user explicitly approved installation and enablement of:
- `~/.config/systemd/user/assignment-agent-lms-scan.service`
- `~/.config/systemd/user/assignment-agent-lms-scan.timer`

Repository source copies are in `deploy/systemd/`. The service is Type=oneshot,
uses `%h/Desktop/assignment-agent` and its `.venv/bin/python -m app.main lms-scan`,
sets ASSIGNMENT_LMS_BACKGROUND=1, desktop D-Bus, UMask=0077, journal output, and a
three-minute timeout. Timer: OnCalendar=*-*-* *:00,30:00, Persistent=true,
AccuracySec=1min. No sudo, extra timer, linger changes, or other system changes.
The timer was enabled at 13:54:36 PKT. Manual service execution finished successfully
at 13:55:16 PKT: auth valid, 2 parsed/existing, 0 new/updated/notified/errors, 1 review.
Oneshot service inactive after successful completion is normal; timer stays active.

Auth expiry: safe exit, desktop login-required notice, private 24-hour cooldown
suppresses repeated notices AND network attempts. Changed session credentials allow
an immediate retry. A successful authenticated scan resets cooldown. No login retry
loop. Existing background lock prevents overlapping scans. Only controlled events
and counts are logged; browser error details and credentials are not logged.

## Current task — multi-course scanning, live preview completed

User requested dynamic discovery and scanning of ALL current registered courses,
with optional include/exclude filters, conservative unknown-course enrichment,
per-course error isolation, aggregate logging, dry-run safety, and mocked tests.
Explicit stopping point: after tests and live discovery/dry-run, show results and
wait for review. DO NOT activate multi-course background scope, restart/re-enable
units, or import the newly discovered assignments before review/authorization.

To avoid changing the active timer's behavior while editing its application code,
background mode remains single-course unless the private target explicitly contains
`multi_course_enabled: true`. This flag has NOT been set. Manual lms-scan defaults
to multi-course; background dry-run also previews multi-course. The old manual flow
remains available with `--single-course`; `--configure-background` is preserved.
No unit modification is needed for eventual multi-course activation.

The user's latest message was "allow but before that save everything in your
memory till we have done". Treat this as permission to finish the interrupted
checkpoint/documentation/test work, NOT as approval to activate wider scheduled
scanning. This file is the durable saved project memory.

## Actual course DOM/navigation — inspected, not guessed

- The LMS's actual Dashboard anchor leads to a Courses Registered table with
  headers: Sr.No., Course Title, Class, Semester.
- Course rows are plain text, with no per-course links or exposed course codes.
- Follow the actual visible Assignments anchor. It provides select#semesterId
  (onchange SelectSemester()) and select#courseId (onchange GetCourses()).
- Verified handlers navigate using the selected semester/course values in s/oc
  query parameters. Code invokes the dropdown controls and waits for navigation;
  it DOES NOT invent URL patterns or construct per-course URLs.
- Registered names must match unique actual course options. Ambiguous identical
  names across sections are skipped. IDs hash the actual term/course option values.
- The DOM has no explicit current-term flag. Use the newest offered labeled
  Spring/Summer/Fall term and only its registered rows; do not trust a historical
  selected option or traverse old semesters. Current verified term: Fall-2026.
- Available historical terms go back to 2019; none were scanned.

## Live multi-course results (read-only, 20 September 2026)

| Current course | Class/section | Assignments |
|---|---|---:|
| Object Oriented Programming | BS (CS)-2 (B) Morning | 0 |
| Linear Algebra | BS (CS)-3 (B) Morning | 0 |
| Artificial Intelligence Lab | BS (CS)-5 (A) Morning | 3 |
| Compiler Construction | BS (CS)-6 (B) Morning | 0 |
| Introduction to Data Science Lab | BS (CS)-6 (A) Morning | 2 |

All five were scanned successfully; no courses skipped or navigation failures.
AI Lab candidates (not imported):
- Lab 1: Introduction to Python IDEs; due Sep 9, 2026, 23:30 PKT; OVERDUE.
- Lab 2: Function, Recursion and OOP; due Sep 12, 2026, 23:30 PKT; OVERDUE.
- Lab 3: Introduction to Python Programming; due Sep 18, 2026, 12:30 PKT;
  SUBMITTED, verified submission-file evidence.
The displayed LMS titles contain additional CLO/PLO text; the parser retains the
actual title. The two Data Science Lab assignments match existing records.

Actual commands successfully executed after tests:
- `.venv/bin/python -m app.main lms-courses --dry-run --verbose`
- `.venv/bin/python -m app.main lms-scan --dry-run --verbose`

Summary: 5 courses discovered/scanned, 0 skipped; 5 assignments parsed, 3 new
candidates, 2 existing, 0 actual updates, 1 needs review, 2 submitted, 3 overdue,
0 notifications, 0 errors; auth valid; exit 0. About 17 seconds for the full scan.
Hash comparisons confirmed SQLite and WAL/SHM (if present), private credentials,
scan target, auth-notice state, and installed service/timer unchanged.
Coverage is the fetched assignment page per current course; pagination remains
unverified. No historical archive traversal. Do not imply all historical records.

## Implementation and tests

New `app/lms/courses.py`:
- verified DOM snapshots, semester policy, registered-course discovery;
- actual visible links and course/semester selector navigation;
- include/exclude filters (comma-separated exact normalized names/exposed codes,
  exclusion wins), environment names LMS_COURSE_INCLUDE / LMS_COURSE_EXCLUDE;
- course provenance, per-course isolation, aggregate counts and read-only discovery.

Modified `app/main.py`, `app/lms/background.py`, `app/lms/scan.py`,
`app/lms/storage.py`, `tests/conftest.py`, `tests/test_lms_scan.py`.
New `tests/test_lms_courses.py` contains 30 synthetic/mocked tests.
- Existing known-course Gmail/LMS exact matching stays unchanged.
- Unknown-course enrichment requires exact title/number/known deadline/term plus
  matching recorded course ID OR uniqueness in a complete, unfiltered, error-free
  survey of all current courses. Ambiguous legacy candidates are left for review;
  no duplicate insert or cross-course merge. Local assignment ID/history retained.
- All fetched records are processed together so pending notifications are attempted
  once per overall scan. Metadata changes do not create new assignment notifications.
- Auth expiry stops further course requests and uses the existing cooldown path.

Most recent completed full validation: compileall succeeded; 356 tests passed
(326 previous baseline + 30 multi-course tests), with no live LMS in pytest.

## Interrupted work and remaining actions

The last attempted documentation/check command was aborted BEFORE execution.
Inspection after interruption confirmed: `docs/LMS_MULTICOURSE.md` did not yet exist;
.env.example and README had not received multi-course additions; no pytest process
was running. Existing code and successful live results above were already saved.

Next authorized actions:
1. Finish concise multi-course documentation and .env.example entries; link from
   README. Correct the aggregate scan's legacy singular coverage wording.
2. Re-run compileall and pytest after that small wording change; fix any failures.
3. Present the discovered courses/counts, zero skipped/errors/writes/notifications,
   validation result, and unchanged timer. Stop awaiting multi-course scope review.
4. Do not rerun live scanning unnecessarily; the real multi-course path already
   passed. No new real import, background activation, or systemd changes yet.

The entire repository appeared untracked in git throughout this session; do not
assume a clean committed baseline, reset it, or discard existing files. No git
commit was requested. Persistent project files are the saved work/checkpoint.

## Resume verification — 24 September 2026

The existing multi-course code and 30 tests were preserved. Completed the missing
`docs/LMS_MULTICOURSE.md`, added LMS filter examples to `.env.example`, updated
README scope/links, and corrected aggregate coverage wording in `app/lms/scan.py`.
Compileall succeeded; all 356 tests passed in 5.22 seconds.

Both requested live commands were attempted again with the existing virtualenv:
`lms-courses --dry-run --verbose` and `lms-scan --dry-run --verbose`.
Both reported `authentication_required` and safe exit 0 before course discovery.
No fresh course list or assignment counts were obtained. September 20 results
above remain historical evidence only. User must refresh the session through
`lms-auth` before fresh live verification can finish; never enter credentials for
the user. Runtime/database, credentials and installed LMS unit content fingerprints
were unchanged across the dry-runs. No imports, notifications, timer modifications,
restarts or multi-course background activation were performed.

## Authentication refreshed and live verification completed — 24 September 2026

User completed login and confirmed the Introduction to Data Science Lab
Assignments tab. `lms-auth` saved the private session and replay-verified course
target. No timer was installed, restarted, or enabled.

Both requested live dry-runs then succeeded with authentication valid and exit 0.
Current term Fall-2026: Object Oriented Programming 0 assignments; Linear Algebra
0; Artificial Intelligence Lab 4; Compiler Construction 0; Introduction to Data
Science Lab 3. All 5 courses scanned, 0 skipped, 0 navigation errors.
Aggregate: 7 parsed, 5 new candidates, 2 existing, 0 actual updates, 4 needing
review, 4 submitted, 3 overdue, 0 notifications, 0 errors. No records imported.
AI Lab 4 (BFS and DFS) is submitted, due September 25 at 23:30 PKT, and marked
for review because its Action anchor is unverified. AI Labs 1 and 2 now also
have conditional September 22 extensions, retained for review alongside Actual
deadlines. Data Science Lab 03 is submitted, due September 23 at 09:00 PKT.

All 11 fingerprinted runtime/database, credential and installed LMS unit files
had unchanged contents across the two dry-runs, using the post-authentication
baseline. Existing 30-minute single-course background scope remains unchanged;
no unit modification is needed for eventual separately authorized multi-course
activation. Prior validation remains compileall success and 356 tests passing.
Pagination and Action/detail navigation remain unverified.

## Multi-course background activation authorized and completed — 24 September 2026

User explicitly authorized the real import and activation of current-course scans
for the existing timer. This supersedes the earlier review gate/stopping point.
Removed the CLI private `multi_course_enabled` gate: normal manual and background
`lms-scan` now use current-course discovery. Explicit `--single-course` remains
available. Updated dispatch tests and README/multi-course documentation. Parser,
Gmail, reminders, notification, authentication and semester policies unchanged.

Real manual scan: 5 courses discovered/scanned, 0 skipped, 7 parsed, 5 new,
2 existing/updated, 4 review, 4 submitted, 3 overdue, 2 notifications, 0 errors.
Assignment listing and read-only SQL confirmed 7 total records, 0 duplicate
course/title groups, 4 submitted, 4 needs_review, 0 pending LMS notifications.
Second manual scan: 0 new, 7 existing, 0 updated, 0 notifications, 0 errors.
Compileall succeeded; 359 pytest tests passed in 5.15 seconds after updating
one legacy dispatch assertion for the now-authorized default.

Manual `systemctl --user start assignment-agent-lms-scan.service` succeeded at
17:55:20 PKT, status 0/SUCCESS: 5 courses discovered/scanned, 0 skipped, 7 parsed,
0 new, 7 existing, 0 updated, 4 review, 0 notifications, 0 errors. Oneshot inactive
(dead) after success is expected. Final SQL checks still show 7 records and no
duplicates. Timer is active, next observed trigger September 24 at 18:00 PKT.
Installed units already use the correct command. No unit edits, daemon-reload,
timer restart, new timer, or schedule changes were needed. Schedule remains
OnCalendar=*-*-* *:00,30:00. Pagination/detail navigation remains unimplemented.

## Gmail notification channel implemented — 24 September 2026

User requested Gmail mobile alerts alongside existing desktop notifications and
explicitly required stopping before any real test email. Implementation complete;
no real email or OAuth reauthorization performed, no real recipient configured,
and no systemd changes made. `ALERT_EMAIL=` is optional and blank disables email.
Exported settings are used; `.env` is not automatically loaded. Scheduled services
need the recipient in their own/user-manager environment after user approval.

OAuth now requires gmail.readonly plus gmail.send. Existing read-only tokens are
rejected with the explicit command `python -m app.main gmail-auth --force`;
background operations never open consent or silently expand permissions. Gmail
scanner behavior otherwise unchanged. `python -m app.main test-email` sends the
specified harmless subject/body only when explicitly invoked; do not run it
until the user approves a real send.

New app/email_notifications.py implements plain-text Gmail API sending and SQLite
notification_events. New assignment email is queued in the same transaction as
new unsubmitted LMS record insertion, not on every scan or metadata update.
Due-tomorrow/due-today emails use existing calendar policy for LMS/provenance
records; submitted or stale pending reminders are cancelled at dispatch. Auth
expiry email uses existing cooldown plus durable 24-hour email protection.
Desktop state/retries remain independent. Gmail errors are sanitized and do not
fail LMS scans or stop desktop notifications. Dry-runs never enqueue/send email.

Deduplication: unique event/channel keys, new once per assignment, reminders once
per type/deadline version. Send claims commit before Gmail POST; no automatic
send retries. Failed or interrupted attempts are retained for review rather than
resent, avoiding duplicates after uncertain delivery. This is at-most-once
attempts, not guaranteed delivery; setup failures/crashes can lose an email.
See docs/EMAIL_NOTIFICATIONS.md for setup and this tradeoff.

Changed: app/gmail_auth.py, app/main.py, app/database.py, app/email_notifications.py,
app/lms/storage.py, app/lms/scan.py, app/lms/background.py, app/scheduler.py,
tests/conftest.py, tests/test_email_notifications.py, tests/test_lms_background.py,
.env.example, README.md, docs/GMAIL_SETUP.md, docs/EMAIL_NOTIFICATIONS.md,
and this checkpoint. Compileall succeeded; 383 tests passed (24 new tests),
all Gmail sending mocked and test databases temporary. No real test email sent.

## Persistent email recipient configured — 24 September 2026

After the email implementation, the user explicitly ran Gmail reauthorization:
read/send consent was saved successfully. The user then approved one real test
email, which Gmail accepted. No further real test emails were sent.

User authorized persistent ALERT_EMAIL for the existing scheduled services.
Created Git-ignored config/.env with the user-provided recipient, permissions
0600. Installed environment.conf drop-ins for assignment-agent-lms-scan.service
and assignment-agent-reminders.service, with matching source copies under
 deploy/systemd/<service>.d/. Both use
EnvironmentFile=%h/Desktop/assignment-agent/config/.env. Daily summaries do not
support email and remain unchanged. No Python hardcoding or timer changes.

Ran daemon-reload once, no service/timer restart. Both loaded service definitions
report the expected EnvironmentFiles and NeedDaemonReload=no. A transient
systemd environment-only process verified the exact recipient successfully;
it did not invoke the app, access OAuth, or send email. The persistent recipient
will be read on subsequent scheduled service invocations. Compileall succeeded;
383 tests passed in 6.50 seconds. Updated docs/EMAIL_NOTIFICATIONS.md accordingly.

## VPS preparation only — 24 September 2026

Added docs/VPS_DEPLOYMENT.md and six separate system unit templates under
 deploy/vps/systemd/ for Ubuntu 24.04, /opt/assignment-agent, and a non-root
assignment-agent account. No VPS purchased/configured/deployed, no source or
private files transferred, no installed units changed, and no email sent.
User requires approval before buying/configuring/deploying a VPS.

Minimal opt-in app change: ASSIGNMENT_NOTIFICATION_DESTINATION=journal routes
local notifications to structured stdout/journald on VPS; default laptop desktop
behavior and independent Gmail events are unchanged. VPS units select this mode.
.env.example and notifier/test-isolation tests document/verify it. Daily summary
remains local-channel only, logged on VPS. Current laptop automation untouched.

Guide covers OS/Python/Playwright dependencies, headless sandbox prerequisites,
private Gmail/Bahria/config files, complete SQLite backup/history migration,
separate future approved cutover to prevent two-host duplicate alerts, and target
validation. All three timer definitions match existing source schedules exactly.
Compileall passed; 384 tests passed in 6.72 seconds. Unit syntax verified using
temporary copies with the local Python executable substituted; target-side paths,
Python 3.12, sandbox and session validation remain deployment prerequisites.

## Manual assignment report implemented — 24 September 2026

Added `python -m app.main email-assignment-report`. It uses the existing scan
lock/session/background lifecycle and multi-course scanner, explicitly bypassing
optional include/exclude filters to cover every discovered current-term course.
Historical terms are not traversed. Local updates use existing LMSStorage matching,
submission evidence and review rules. Only rows from the fresh scan are reported.

One plain-text email is sent with subject Bahria Assignment Report — Current
Semester, summary counts and groups including empty courses. Available full titles,
numbers, Asia/Karachi deadlines, conflict/extension raw evidence, observed LMS
submission state, saved local status, review flags, marks/comments and verified
links are included. Unresolved deadlines say Needs review. Extra existing workflow
states map to IN_PROGRESS for display with exact stored state also shown; records
are not changed merely for display. Pagination/detail navigation remains unverified
and coverage is stated in the email.

Report scans suppress both desktop and email new-event dispatch, preserving
queued genuine new events for the normal scanner and never resetting histories.
Busy/auth/partial/parse/import failures prevent the report email. Successful
partial imports may remain when another course fails. Reports are explicit once
per invocation, with no automatic scheduling/retry. ALERT_EMAIL must be exported;
manual commands do not automatically load config/.env.

Changed: app/assignment_report.py, app/main.py, app/lms/background.py,
app/lms/courses.py, app/lms/scan.py, tests/test_assignment_report.py, README.md,
docs/EMAIL_NOTIFICATIONS.md, and this checkpoint. Compileall succeeded; 399 tests
passed in 7.41 seconds, including 15 report tests. All report email/browser calls
were mocked. No live scan or report email was run. Existing timers untouched.
User explicitly requested stopping before sending a real report; await approval.
