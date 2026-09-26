# Gmail assignment notifications

Email is an independent notification channel. Gmail scanning itself never sends
mail. The existing LMS and reminder schedules remain unchanged. No email is sent
merely because a 30-minute scan runs, and enabling email does not replay old
new-assignment alerts. Blank `ALERT_EMAIL` disables email without changing desktop
notifications.

## Explicit authorization and configuration

The required OAuth scopes are:

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/gmail.send`

Add both scopes to your Google Cloud consent configuration. Existing read-only
tokens are rejected with an actionable message, without opening a browser or
silently expanding access. Reauthorize explicitly from the virtual environment:

```bash
source .venv/bin/activate
python -m app.main gmail-auth --force
export ALERT_EMAIL="your-address@example.com"
```

Use the destination address you want; none is hardcoded. `.env.example` documents
the setting, but the application does not automatically load `.env`.
No Gmail password or app password is used. The sender is your OAuth-authorized
Gmail account. Plain-text MIME is sent using Gmail API `users.messages.send`, as
specified in Google's [sending guide](https://developers.google.com/workspace/gmail/api/guides/sending).
See Google's [scope definitions](https://developers.google.com/workspace/gmail/api/auth/scopes).

After approving a real test send, run:

```bash
python -m app.main test-email
```

This explicitly sends one message per invocation to `ALERT_EMAIL`:
Subject `Assignment Agent Test`; body
`Your Assignment Agent Gmail notifications are working.`
The test command does not create assignment or notification-event records.

The existing LMS scan and deadline reminder user services load
`config/.env` through their `environment.conf` systemd drop-ins. The file is
Git-ignored and owner-readable/writable only (0600). It contains `ALERT_EMAIL`;
OAuth tokens remain in their existing private credential files. No email address
is hardcoded in Python. Daily summaries remain desktop-only.

Systemd reads the file on each service invocation. After installing/changing the
drop-ins, run `systemctl --user daemon-reload`; no timer or service restart is
needed. Editing only the recipient in `config/.env` requires no daemon reload.
Timer schedules are unchanged. Manual CLI commands still use exported variables;
the Python application does not automatically load `.env` files.

## Events and content

- `NEW_ASSIGNMENT`: queued transactionally only when a new, unsubmitted LMS
  assignment is inserted. Existing records, metadata changes and submitted
  assignments do not generate new emails.
- `DUE_TOMORROW` and `DUE_TODAY`: use the existing Asia/Karachi calendar-day
  policy, only for LMS records (including Gmail records with LMS provenance).
  No three-day, six-hour, one-hour or overdue reminder is added.
- `LMS_AUTH_EXPIRED`: uses the existing scan lock and 24-hour auth cooldown,
  with a durable email guard. It includes the `lms-auth` recovery command.

Assignment emails include course, title, deadline, submission status, and a
reliable LMS link when stored. Submitted records are checked again at dispatch
and pending assignment emails are cancelled. Deadline reminders that no longer
match the current date/deadline are also cancelled.

## Duplicate prevention and failure behavior

SQLite `notification_events` stores the assignment ID (null for authentication),
channel, event type, deadline version, attempted time, sent time and state. A
unique `(event_key, channel)` key and transactional claim serialize concurrent
senders. New email is once per local assignment; reminders are once per type
and deadline version, matching existing desktop reminder history semantics.
Changing recipients does not replay events. Deleting an assignment removes its
email events so reused IDs do not suppress a new record.

Desktop delivery retains its existing independent histories and retries. Email
is dispatched even if desktop delivery fails; email failure is logged with
sanitized text and does not fail the LMS scan or stop desktop reminders.
Dry-run performs no email event writes or Gmail sends.

To prioritize duplicate prevention, email attempts are committed BEFORE the
Gmail POST and the send request has no automatic retries. A network error may
mean Gmail accepted a message but its response was lost. Failed or interrupted
attempts therefore remain `failed` or `attempted` for manual review and are not
automatically resent, even after reauthorization. This provides at-most-once
attempts, not guaranteed delivery: a crash before the POST can lose an alert.
`sent_at` is populated only after Gmail confirms success; credentials and raw
API errors are never included in logs. Set up authorization and test delivery
before enabling scheduled email to avoid losing events to configuration errors.

All tests use temporary databases and mocked Gmail calls. No real test email was
sent during implementation.

## Manual current-semester assignment report

```bash
source .venv/bin/activate
set -a
source config/.env
set +a
python -m app.main email-assignment-report
```

Run only when you intend to send a real report. Each explicit invocation performs
a fresh headless scan, updates local records through the existing deduplication
and submission-evidence rules, and sends one plain-text email to `ALERT_EMAIL`.
Subject: `Bahria Assignment Report — Current Semester`. No report timer is added.

The report deliberately scans all current registered courses, ignoring optional
include/exclude filters for this command only. Historical semesters are not
visited. Only assignments visible in the newly fetched course tables appear;
old database-only records are not included. Pagination/detail navigation remains
unverified and this coverage limit is stated in the email.

Summary counts precede course groups, including `No assignments found.` for empty
courses. Each entry includes full available title, number, Karachi deadline,
conflict/extension evidence, observed LMS submission status, stored local status,
review flag, marks/comments and a reliable link when available. Unresolved dates
show `Deadline: Needs review`. Existing REVIEWED/READY_TO_SUBMIT workflow states
are displayed under IN_PROGRESS with the exact stored state on a separate line;
the database state is not changed. Summary submitted/not-submitted counts use
local status, which may include your manual submission confirmation. Observed
LMS submission status remains separate. Overdue counts include unsubmitted
assignments with past known deadlines, even when their workflow is IN_PROGRESS.

No desktop/new-assignment emails are dispatched by a report scan. Genuine new
assignment events remain queued for the normal scanner; existing event history
is not cleared or replayed. Reminder behavior stays unchanged. A failed/skipped
course, parsing/storage error, busy scan lock, or unavailable authentication
prevents the report from being sent. Successfully fetched records may already
have been updated if another course fails. Gmail failure does not roll back
those updates. There is no automatic retry of the report send; invoking the
command again is a new explicit request for a report.

This command was verified with mocked Gmail/browser calls only during
implementation; no real report email was sent.
