# Student browser sessions

This guide describes the implemented `student-auth`, `student-scan`, and
`scan-all-students` workflows. For installation and Gmail setup, start with the
[README](../README.md). The legacy `lms-*` commands retain their separate JSON
snapshot workflow and do not select a registered student's persistent profile.

## Register and authenticate

Run commands from the project root with its virtual environment activated.
`student-example` is a generic example for `<student-id>`; replace it with your
registered ID. Do not paste angle-bracket placeholders unquoted into a shell.

```bash
cd /path/to/assignment-agent
source .venv/bin/activate
python -m app.main student-add --id student-example
python -m app.main student-list
python -m app.main student-auth student-example
```

Registration prompts for a name and notification email. IDs use lowercase
letters, digits, and single separating hyphens. `student-list` prints personal
registry information; do not publish its output without reviewing it.

Authentication requires a display on the machine and OS account that own the
scanner's profile. At first use, supply the HTTPS portal address when prompted.
Use the existing signed-in portal if available; otherwise the student enters
their own credentials and completes MFA/CAPTCHA directly in Chromium.

Confirm the authenticated portal in the terminal. Then open **Go To LMS**, a
current course, and its **Assignments** page as instructed. Select the LMS tab
and confirm the different origin if prompted. The command checks the assignment
page and saves a verified scan target before reporting success.

## Persistent profile and snapshot compatibility

Each student has a dedicated private directory:

```text
credentials/students/<student-id>/
    bahria_storage_state.json
    bahria_portal.json
    bahria_scan_target.json
    lms-scan.lock
    browser-profile/
        .initialized
```

`browser-profile/` is a persistent Chromium user-data directory reused by manual
authentication and headless scans. The JSON `storage_state` snapshot remains for
backward compatibility and includes browser storage exported with IndexedDB.
These are authentication secrets, not files to publish or inspect in support logs.

The first profile launch imports an existing valid snapshot using
`set_storage_state`. The initialization marker is written after successful import;
an import failure leaves the snapshot intact and permits a later retry. Without
an existing snapshot, manual authentication can initialize a new profile.

Later launches reuse the profile rather than replacing its local storage,
IndexedDB, or persistent cookies. Missing session cookies can be restored from
the snapshot because Chromium may drop them between browser processes. Cookies
already present in the profile take precedence. Successful authentication and
normal scans refresh the compatibility snapshot. An initialized profile can be
used without a snapshot, but a malformed or insecure snapshot that is present
can still cause a scan error. Profile existence alone does not prove valid login.

## Isolation and concurrent access

Student paths must resolve through the expected project directory
`credentials/students/<student-id>/bahria_storage_state.json`. Shared paths,
traversal-style IDs, and symlinked authentication directories are rejected.
Changing the assignment data directory does not relocate student profiles.

Authentication and scanning acquire the same nonblocking per-student lock through
browser closure and state updates. A busy student returns a nonzero result;
other students use different locks. Wait for the active operation to finish.
Do not delete lock files or redirect one student's profile to another directory.

Assignments and event recipients are scoped through the student registry.
`scan-all-students` attempts each enabled student sequentially, continues after a
failure, and returns a failure result when any student fails. This is separation
inside one application and OS account, not an OS-level security boundary between
students. The local operator can access all stored data.

## Authentication checks and session recovery

Manual portal validation rejects visible login/password fields and unexpected
portal-origin changes. The user must confirm the signed-in portal. This is a
conservative check: absence of a login form alone is not proof of authentication.
The additional assignment-page verification establishes the scan target.

During scanning, HTTP 401/403, visible password fields, login URLs, and a visible
Sign In link indicate authentication is required. Other HTTP, network, browser,
configuration, or parsing failures are reported as scan errors; they are not
proof of expiry or successful login.

If a scan with a persistent profile detects expiry, it can make one recovery
attempt through the configured HTTPS Bahria portal. It checks the portal, finds
exactly one visible **Go To LMS** link, and accepts only an HTTPS Bahria destination
without embedded username/password information. Recovery must end on the LMS
host, then the scan is retried once. Missing, ambiguous, JavaScript-only, or
external links are not guessed. An absent session cannot use this recovery path.
The application does not fill a login form or bypass authentication.

## Scanning and LOGIN_REQUIRED

```bash
python -m app.main student-scan student-example --dry-run
python -m app.main student-scan student-example
python -m app.main scan-all-students --dry-run
python -m app.main scan-all-students
```

Student scans discover current courses using that student's saved target. Normal
scans reconcile assignment data and may send notifications. A completed browser
scan marks the student's authentication status `ACTIVE`; this status is not a
guarantee that every course parsed successfully. Inspect the result and exit code.

When a normal scan has no usable session or still detects expiry after recovery,
it marks only that student `LOGIN_REQUIRED`, attempts authentication notices, and
records a 24-hour scan retry cooldown. A matching snapshot fingerprint and an
unexpired retry deadline suppress another normal attempt. Expiry, cooldown, a
busy lock, or another scan failure produces a nonzero student-scan result.

Reauthenticate and scan again:

```bash
python -m app.main student-auth student-example
python -m app.main student-scan student-example
```

Successful reauthentication explicitly clears that student's scan notice while
holding the lock and sets `ACTIVE`. The next scan can run even if the snapshot
bytes did not change. Failed or cancelled authentication does not perform that
success-path reset. Notification audit history is preserved.

## Notification behavior

Authentication expiry attempts a desktop notice and a Gmail event addressed to
the enabled student's registered email. The notice instructs the operator to run
`student-auth` for that student. Delivery depends on working desktop/Gmail setup;
a queued event or attempted send does not guarantee receipt.

The scan notice limits repeat desktop alerts and scan attempts. Gmail has its
own per-student 24-hour cooldown based on authentication-alert attempts.
Reauthentication clears the scan cooldown, not the independent Gmail audit
history or email cooldown. It does not resend past assignment notifications.
The legacy scanner has no explicit student owner for authentication email and
retains its desktop-notice behavior.

## Dry-run and cancellation

`--dry-run` bypasses scan cooldown suppression and skips assignment reconciliation
writes, authentication-status changes, scan-notice writes, and notifications.
It still launches Chromium, can bootstrap a profile, and can attempt session
recovery. CLI startup can initialize the database/schema. Browser-managed state
can change, so dry-run is not an entirely write-free operation or a profile backup.

Cancelling manual authentication before confirmation preserves the prior JSON
snapshot, but browser profile changes already made during the interaction can
remain. Do not use cancellation as a way to roll back a browser session.

## Security model and password handling

- Students enter passwords only into Bahria's browser page. Application code does
  not request, read, fill, or store password input values in its configuration,
  database, or logs. The browser necessarily handles the login interaction.
- Before profile launch, preferences disable password saving and address/payment
  autofill. This does not assert that a previously used profile contains no old
  browser data. Use the dedicated application profile, not a personal profile.
- Authentication directories are restricted to mode `0700`. JSON snapshots are
  written privately, and new lock files use mode `0600`. Chromium inherits a
  restrictive `0077` umask. These controls are not encryption.
- Chromium sandboxing and TLS checks remain enabled. Playwright debug environment
  variables are suppressed during the browser lifecycle, and browser failure
  diagnostics are redacted to avoid exposing authenticated navigation data.
- Profiles, snapshots, OAuth files, databases, and logs must remain private.
  `.gitignore` excludes common runtime paths and key files, but cannot prevent
  deliberate force-adds or protect files shared outside Git.
- Student names, email addresses, and assignment information can appear in normal
  operator output. Review terminal output and journals before sharing them.

## Limits and troubleshooting

Bahria controls session expiration, revocation, login policy, and MFA/CAPTCHA.
Persistent profiles cannot extend a server-expired session or promise a fixed
login lifetime. Tab-specific session storage is not guaranteed to survive a
restart. Manual authentication remains necessary when recovery cannot restore
access. Navigation changes can also require a newly verified target.

For `LOGIN_REQUIRED`, run `student-auth` with the student present. For a busy lock,
wait and retry. For scan errors, check connectivity, Chromium dependencies, file
permissions, and portal/target configuration without dumping private state.
Do not disable browser security or delete profiles to work around errors.

The browser lifecycle changes process-wide environment variables and umask; it
is designed for synchronous CLI use, not concurrent calls from a threaded server.
Scheduled jobs must run under the same OS account with access to the correct
project, database, and profiles. Existing deployment templates use legacy
`lms-scan`; multi-student scheduling requires an explicitly configured command.

## Development validation

```bash
python -m compileall -q app
python -m pytest -q tests/test_student_sessions.py
python -m pytest -q
```

Tests use synthetic sessions, temporary storage, and mocked browser/notification
calls. They cover profile bootstrap, isolation, locks, bounded recovery,
reauthentication cooldown reset, and redaction. The session fixtures apply audit
schema columns to model a migrated deployment; passing these tests alone does
not establish fresh-database migration completeness or live Bahria/Gmail behavior.
Use current test output for results rather than historical counts in this guide.
