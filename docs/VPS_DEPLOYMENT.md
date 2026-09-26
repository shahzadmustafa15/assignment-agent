# Ubuntu 24.04 VPS deployment preparation

Prepared only: no VPS purchased/configured, files transferred, units installed,
laptop automation changed, or email sent. Obtain approval before any deployment
or laptop-to-VPS cutover. All commands below are future instructions, not actions
performed during preparation. Do not install these units on the laptop.

## Server and runtime requirements

Use Ubuntu 24.04 LTS with systemd and SSH access, a non-root application account,
working DNS/outbound HTTPS to Google and Bahria, accurate system time, and local
persistent storage for SQLite. No public application port is required. A planning
baseline is 2 GB RAM and 10 GB free disk; actual Chromium use must be measured.

First command, on the VPS only after approval:

```bash
sudo apt-get update
```

Install OS tools:

```bash
sudo apt-get install -y python3 python3-venv python3-pip ca-certificates tzdata sqlite3 rsync openssh-client
sudo useradd --system --create-home --home-dir /var/lib/assignment-agent --shell /usr/sbin/nologin assignment-agent
sudo install -d -o assignment-agent -g assignment-agent -m 0750 /opt/assignment-agent
```

Skip account creation if it already exists. Ubuntu 24.04's Python 3.12 is the
intended server interpreter; current laptop tests use Python 3.13. Run the full
tests again on the server before activation. Install the dependencies from
`requirements.txt` into a fresh virtualenv; never copy the laptop `.venv`.
The requirements include Gmail API/OAuth libraries, pytest and Playwright
`>=1.51,<2`; the checkpoint's verified Playwright version is 1.63.0. To reproduce
that browser stack, use the version constraint below rather than silently taking
a different Playwright release. If unavailable on the server, stop and investigate.

Transfer reviewed application source into `/opt/assignment-agent` using SSH and
an explicit allowlist: `app/`, `tests/`, `docs/`, `deploy/vps/`, `requirements.txt`,
`pytest.ini`, `.env.example`, and `.gitignore`. Do not blindly archive the working
tree: it currently has no committed baseline and contains ignored private data.
Keep credentials, configuration, database, browser cache and logs out of source
control and public build artifacts. Set application file ownership to the service
account before creating its virtualenv.

On the VPS after source transfer:

```bash
sudo -u assignment-agent -H python3 -m venv /opt/assignment-agent/.venv
sudo -u assignment-agent -H /opt/assignment-agent/.venv/bin/python -m pip install -r /opt/assignment-agent/requirements.txt 'playwright==1.63.0'
sudo /opt/assignment-agent/.venv/bin/python -m playwright install-deps chromium
sudo -u assignment-agent -H env PLAYWRIGHT_BROWSERS_PATH=/var/lib/assignment-agent/ms-playwright /opt/assignment-agent/.venv/bin/python -m playwright install chromium
```

`install-deps chromium` installs the OS shared libraries/fonts appropriate to the
installed Playwright release (including NSS, graphics, audio and font libraries).
Use this supported dependency list instead of a hand-maintained list of Ubuntu
library names. Browser binaries must match the Python Playwright version and be
installed for the service account at the same path used by the units.
[Playwright browser/dependency installation](https://playwright.dev/python/docs/browsers)
and [supported platforms](https://playwright.dev/python/docs/intro).

## Headless execution and notifications

The LMS background path already launches Chromium headlessly with sandboxing
explicitly enabled. No X server, Xvfb, desktop login, or `DISPLAY` is needed for
normal scans. Do not run Chromium as root or disable its sandbox/TLS validation.
The host must permit Chromium's sandbox: Ubuntu AppArmor/user-namespace policy
can block downloaded browsers. If launch fails, review kernel/AppArmor logs and
have the server administrator configure a narrowly scoped policy for the actual
browser executable. The existing adapter also recognizes a trusted, root-owned,
setuid `/opt/google/chrome/chrome-sandbox` helper when installed appropriately.
Do not copy a helper from the laptop or globally disable namespace restrictions.
[Ubuntu namespace restrictions](https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces).
Sandbox startup is a deployment acceptance check, not assumed verified here.

VPS units explicitly select `ASSIGNMENT_NOTIFICATION_DESTINATION=journal`.
This routes the local notification channel to structured stdout/journald, avoiding
missing `notify-send`/D-Bus errors. Successful journal handoff uses the existing
local notification history; Gmail remains an independent channel with its own
history. Laptop defaults still use Ubuntu desktop notifications. No GUI packages
or notification daemon are required for this VPS mode. Journals contain assignment
alert text, so restrict access and retain them according to your privacy needs.

Gmail mobile alerts retain NEW_ASSIGNMENT, DUE_TOMORROW, DUE_TODAY, and the existing
LMS-auth cooldown. No email is sent just because a scan runs. Failed/uncertain
Gmail attempts are not automatically resent; see `EMAIL_NOTIFICATIONS.md`.
Daily summaries remain local-channel only (journal on the VPS), not email.
Gmail scanning remains available manually; no Gmail scanning timer is added.

## Private files: transfer separately over SSH

Preserve relative paths under `/opt/assignment-agent`:

| File | Purpose |
| --- | --- |
| `credentials/gmail_credentials.json` | Google Desktop OAuth client |
| `credentials/gmail_token.json` | Authorized read/send token |
| `credentials/bahria_storage_state.json` | Playwright cookies/storage; treat as a password |
| `credentials/bahria_portal.json` | Portal configuration used by LMS commands |
| `credentials/bahria_scan_target.json` | Verified navigation starting point required by background scans |
| `config/.env` | `ALERT_EMAIL` and any explicitly configured filters/settings |
| `data/assignments.db` | Consistent database snapshot including all delivery history |
| `data/lms-auth-notice.json` (if present) | Auth-expiry cooldown/fingerprint history |

Use owner-only directories (0700) for credentials/config/data and files (0600),
owned by `assignment-agent`. Do not display token contents or place credentials
in unit files, command-line arguments, Git, or the guide. Review `config/.env`
privately for laptop-specific absolute paths; defaults resolve to the VPS project.
The units load it via `EnvironmentFile`, while Python does not auto-load `.env`.
Manual CLI invocations must explicitly export the intended settings. Do not copy
`data/lms-scan.lock`, sockets, live WAL/SHM files, `.venv`, or browser caches.

OAuth already has `gmail.readonly` and `gmail.send`. Refresh can work headlessly,
but initial consent/reconsent and Bahria credential/CAPTCHA entry remain manual.
For recovery, authenticate on the trusted laptop and securely transfer refreshed
session files while the VPS service is idle. Do not run headed `lms-auth` over a
plain SSH session or automate password entry. Transferred Bahria sessions may be
rejected from a new IP; verify them on the server. If necessary, plan an approved
interactive login environment. OAuth testing-mode tokens may need periodic
reauthorization; always-on hosting does not remove that requirement.

## Safe SQLite migration and cutover

Preparation must leave the laptop timers running. A consistent trial snapshot
can be taken with SQLite's online backup API without stopping writers. For the
final cutover, first obtain approval to stop laptop assignment-writing/alerting
jobs, wait for active runs to finish, and prohibit manual imports during transfer.
Do not run active alerting on both hosts: deduplication is per database and two
copies can send duplicates. Keep VPS timers disabled until final state is copied.

Example snapshot command, run later on the laptop from the project directory:

```bash
umask 077
sqlite3 data/assignments.db ".backup '/tmp/assignment-agent-migration.sqlite'"
sqlite3 /tmp/assignment-agent-migration.sqlite 'PRAGMA integrity_check;'
```

Use a fresh destination path. Transfer that standalone backup over SSH to a
private VPS staging path; compare SHA-256 checksums on both hosts. With all VPS
services stopped, install it as `data/assignments.db`, owned by the service user
and mode 0600. Do not overlay a running database or mix it with stale WAL/SHM
files; archive the complete previous database set if replacing an existing one.

Migrate the entire database, not just assignments: `lms_records`, `gmail_messages`,
`reminder_events`, legacy history, and `notification_events` preserve identity,
submission/review state and sent/attempted notification records. Keep a protected
backup until validation succeeds. Check integrity, row counts, statuses and review
flags against the source. Never clear notification history to force a test.
Copy cooldown state after writers are quiescent. Newer VPS writes must be migrated
back before any rollback to laptop automation, otherwise emails may be replayed.

## Separate VPS system units

Templates in `deploy/vps/systemd/` are system units with `User=assignment-agent`
and `/opt/assignment-agent` paths. Existing laptop user units and drop-ins are
unchanged. System units do not require a logged-in session or user lingering.

- LMS: `OnCalendar=*-*-* *:00,30:00`, persistent, same 30-minute interval.
- Reminder checker: two minutes after manager startup, then every five minutes.
  Reminder eligibility stays the day before and the deadline day in Asia/Karachi.
- Summary: 20:00 Asia/Karachi, persistent, journal only.

After installation prerequisites/private-file migration, and only with approval:

```bash
sudo install -m 0644 /opt/assignment-agent/deploy/vps/systemd/*.service /opt/assignment-agent/deploy/vps/systemd/*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/assignment-agent-*.service /etc/systemd/system/assignment-agent-*.timer
sudo systemctl daemon-reload
```

Installation/reload is separate from enabling timers. At final approved cutover,
after all validation and laptop jobs have been stopped:

```bash
sudo systemctl enable --now assignment-agent-lms-scan.timer assignment-agent-reminders.timer assignment-agent-summary.timer
sudo systemctl list-timers 'assignment-agent-*' --all --no-pager
```

Persistent timers can trigger immediately on activation. That can import records
and send eligible emails; do not enable during a no-email validation phase.
No installation/activation command in this guide has been run by this preparation.

## Deployment validation checklist

- [ ] Source contains no secrets; private files transferred separately, correct owner/modes.
- [ ] `config/.env` has the intended recipient; no laptop path overrides remain.
- [ ] No parallel active laptop/VPS writers during final cutover; full database history retained.
- [ ] Database `PRAGMA integrity_check` returns `ok`; counts/statuses/review flags match.
- [ ] In `/opt/assignment-agent`, run `.venv/bin/python -m compileall app` and
      `.venv/bin/python -m pytest` as the service user; tests send no real email.
- [ ] Service-user Chromium sandbox startup succeeds with the configured browser path.
- [ ] As the service user, with `PLAYWRIGHT_BROWSERS_PATH` matching the units, run
      `.venv/bin/python -m app.main lms-courses --dry-run --verbose` and
      `.venv/bin/python -m app.main lms-scan --dry-run --verbose`.
      Require `authentication_valid` and current-course results: exit 0 alone also
      occurs for expired auth. Verify database unchanged and no notifications.
- [ ] `gmail-status` verifies saved OAuth without sending mail (may refresh its token).
- [ ] Units validate on the target, reference the correct `EnvironmentFile`, and
      run as the non-root service account. Inspect selected systemctl properties,
      never print credential files or the full environment.
- [ ] Do not run `test-email`, real `lms-scan`, `check-reminders`, or enable timers
      during no-email validation. Request approval before live delivery checks.
- [ ] After approved activation, inspect `systemctl status` and `journalctl -u`
      for multi-course totals, auth validity, no errors and no repeated emails.
- [ ] Verify next triggers: LMS every 30 minutes, reminders every five minutes,
      summary 20:00 Asia/Karachi. Confirm Gmail arrival only with approved live tests.
- [ ] Document auth renewal, private backups, log access and rollback ownership.

Local tests and unit-template checks are preparation evidence only; Ubuntu 24.04,
VPS network access, Chromium sandbox and migrated sessions still need target-side
validation. Pagination/detail-page navigation remains outside this deployment.
