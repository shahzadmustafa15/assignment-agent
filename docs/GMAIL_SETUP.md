# Manual Gmail setup — Phase 4

The code is ready, but Gmail cannot connect until you create a Google Cloud
Desktop OAuth client and personally authorize it. No credentials have been
fabricated, and no real mailbox was used in tests. Never provide your Gmail
password, OAuth client JSON, or token contents in chat or commit them to Git.

## Google Cloud steps

1. Open the [Google Cloud Console](https://console.cloud.google.com/) in your
   normal browser. Create or select a project, for example `Assignment Agent`.
2. Open **APIs & Services → Library**, search for **Gmail API**, and enable it
   for that project.
3. Open **Google Auth Platform → Branding**. Use **Get started** if prompted.
   Enter an app name, your support email, and developer contact email.
4. Under **Audience**, use **External** for a personal Google account, leave the
   app in **Testing**, and add the exact Gmail account you will connect under
   **Test users**. For an organization-owned project, Internal is appropriate
   only if that option is available and your account belongs to that organization.
5. Under **Data Access**, add these two scopes:
   `https://www.googleapis.com/auth/gmail.readonly` and
   `https://www.googleapis.com/auth/gmail.send`.
6. Under **Clients → Create client**, choose **Desktop app**, give it a name,
   and create it. Download its JSON. Do not create a service account or Web client.
7. Save the downloaded JSON at:
   `~/assignment-agent/credentials/gmail_credentials.json`.
   The directory already exists with owner-only permissions. Keep the token file
   absent; the app creates it after consent.

These steps follow Google's [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
and [OAuth consent configuration guide](https://developers.google.com/workspace/guides/configure-oauth-consent).
The application uses the official installed-app flow with PKCE and a temporary
127.0.0.1 loopback listener, as described in Google's
[desktop OAuth documentation](https://developers.google.com/identity/protocols/oauth2/native-app).

## Authorize and scan manually

After saving the downloaded client file:

```bash
cd ~/Desktop/assignment-agent
source .venv/bin/activate
chmod 600 credentials/gmail_credentials.json
python -m app.main gmail-auth
```

Your normal browser opens Google's authorization page. Choose the intended Gmail
account and approve read and send access for your own app. The app waits up to three
minutes. If authorization is blocked, check the test user, client type, enabled
API, or your organization's policy. Do not bypass an administrator's restriction.

Then run:

```bash
python -m app.main gmail-status
python -m app.main gmail-scan --dry-run --verbose
# Review classifications before importing:
python -m app.main gmail-scan
python -m app.main assignments
```

`gmail-status` reports missing authorization or verifies a saved token with a
read-only profile request. It may refresh an expired token, but never opens a
browser or prints the token or account address. Network failure is reported as
an inability to verify, not as proof that the account is unauthorized.

`gmail-scan` scans received messages from the last seven days, excluding spam,
trash, sent mail, and drafts. It considers at most 100 search-matching candidate IDs per run by default
and reports if more remain. It never marks mail read, labels messages, sends
email, follows links, downloads file attachments, or submits assignments.

For expired/revoked authorization, or a malformed saved token:

```bash
python -m app.main gmail-auth --force
```

External OAuth apps in **Testing** generally receive refresh tokens that expire
after seven days when requesting Gmail access. Reauthorize when prompted; this
is a Google policy, not a scheduler failure. See Google's
[refresh-token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).

## Scope and local storage

The required scopes are `gmail.readonly` (restricted) and `gmail.send` (sensitive).
The former allows reading mailbox content; the latter allows sending email. Google does not offer an OAuth scope restricted to just
assignment emails. The configured scan window and sender filters are local app
restrictions. See Google's [Gmail scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes).

- Client: `credentials/gmail_credentials.json` (downloaded manually).
- Token: `credentials/gmail_token.json` (created after authorization).
- Directory permissions: `0700`; client and token file permissions: `0600`.
- Token writes use an owner-only temporary file and atomic replacement.
- The whole credentials directory is ignored by Git. Files are permission-protected,
  not encrypted; anyone controlling your user account can access them.

OAuth client/token paths can move together via `GMAIL_CREDENTIALS_DIR`; use a
  dedicated private directory. Existing local reminder commands do not load
Gmail settings or Google libraries.

## Configuration

The app reads exported environment variables, not `.env` automatically. You can
edit your own `.env` using `.env.example`, then load it in Bash with `set -a;
source .env; set +a`. Only source files you trust.

| Variable | Default / meaning |
| --- | --- |
| `GMAIL_CREDENTIALS_DIR` | `credentials`, relative to project root |
| `GMAIL_SCAN_DAYS` | `7`; allowed range 1–365 |
| `GMAIL_MAX_MESSAGES` | `100` candidate IDs; allowed range 1–10000 |
| `GMAIL_FETCH_DELAY_MS` | `100` milliseconds between full-message fetches; range 0–5000 |
| `GMAIL_MAX_RETRIES` | `4` retries per request; range 0–6 |
| `GMAIL_UNREAD_ONLY` | `false`; optionally add `is:unread` to the search |
| `GMAIL_SEARCH_TERMS` | First-query terms: `assignment,assignments,homework,coursework,quiz,assessment,graded task`; 1–30 plain word/phrase terms |
| `GMAIL_DISCOVERY_TERMS` | Second-query terms: `deadline,due,submission,LMS,course,class`; 1–30 plain word/phrase terms |
| `GMAIL_FALLBACK_MESSAGES` | `25` recent metadata candidates if both queries are empty; 0 disables, maximum 50 |
| `GMAIL_KEYWORDS` | Comma-separated candidate/reporting hints; never sufficient for acceptance |
| `ASSIGNMENT_SCORE_THRESHOLD` | `6` (1–100); unknown senders require two additional points |
| `TRUSTED_ACADEMIC_SENDERS` | Optional comma-separated exact addresses or wildcard address patterns |
| `TRUSTED_ACADEMIC_DOMAINS` | Optional comma-separated domain patterns; exact domains also match subdomains |
| `GMAIL_UNIVERSITY_DOMAIN` | Optional domain, matching itself and subdomains |
| `GMAIL_LMS_SENDER` | Optional exact sender email address |
| `GMAIL_TEACHER_EMAILS` | Optional comma-separated exact addresses |
| `GMAIL_SENDER_PATTERNS` | Optional comma-separated shell-style address patterns, e.g. `course-*@example.edu` |

No sender restriction applies when all sender settings are empty. When configured,
matching any sender rule passes the sender filter; weighted classification still applies. Rules
match the parsed address, not its display name. They are relevance filters, not
proof of sender identity. Payment and marketing terminology reduces confidence;
strong assignment evidence and the configured score threshold are required.

## Extraction and review

Plain text and HTML bodies are handled recursively, with MIME depth, part-count,
and size limits. Plain text is preferred in alternative bodies. HTML scripts and
styles are discarded and links are extracted without being opened. Unnamed text
parts stored separately by Gmail can be read using its attachment-body API;
actual file attachments are not downloaded.

The parser retains subject, sender, receipt time, and instructions. It extracts
explicit course/title/teacher fields; a bracketed subject prefix is a course
candidate. The subject serves as title when no explicit assignment title exists.
Missing course is visibly labeled `Unknown course`, not guessed. Sender display
name is not assumed to be the teacher. A single course/assignment/LMS-looking
link is retained as a candidate; ambiguous links remain unset.

Deadlines require an explicit year, unambiguous date, and time on the same
labeled deadline line. Supported forms include ISO dates, named English months,
24-hour or AM/PM times, PKT/Asia/Karachi, UTC/GMT/Z, and numeric timezone offsets.
Absent timezone defaults to Asia/Karachi. Missing time, relative dates, ambiguous
numeric dates, unsupported zones, and conflicting deadlines remain unset.
The original text remains available for review. Receipt time is stored as email
metadata; it is not falsely assigned as the LMS upload time.

All Gmail imports initially have `needs_review=True`, including apparently
complete parses. One email produces at most one reviewable assignment candidate;
digests containing multiple assignments need manual review. Existing reminder
logic acts on known deadlines even while review is pending, so verify imported
dates before relying on them.

## Deduplication and notification behavior

An additive `gmail_messages` SQLite table stores message ID, linked assignment,
subject/sender/receipt metadata, processing outcome/time, and notification time.
Unrelated messages retain only their ID, receipt time, and processing outcome;
their subject/body is not persisted. Existing assignment/reminder records stay intact.

Import and message tracking commit in one transaction. Gmail message IDs and the
existing course/title/deadline rule both prevent duplicate assignments. A message
already processed is skipped before fetching its body. Processed ignored messages
are also skipped on later scans, even if keyword/sender configuration changes;
set filters before the first real scan. Deleting an assignment does not clear its
processed message marker. Gmail messages already represented in existing assignment
rows are recognized even if no new processing marker exists yet.

Only a newly created assignment queues `New Assignment Detected`, including course,
title, and known deadline. Successfully sent notifications are tracked separately;
failed sends remain pending for the next successful manual scan. Duplicate emails
never enqueue another notification. Concurrent scanners serialize import and
notification operations. As with deadline reminders, a process crash between
desktop delivery and the SQLite commit can cause a retry of that notification.

## Existing timers

No systemd files were created, modified, reinstalled, reloaded, or restarted for
Gmail. Your five-minute local reminder timer and 8 PM Asia/Karachi summary continue
unchanged. Gmail scanning remains manual until you explicitly authorize a later
scheduling phase. No Bahria LMS, Playwright, browser automation, or external
assignment submission has been added.


## Stricter assignment classification

`app/assignment_classifier.py` returns `classification` (`ASSIGNMENT` or
`REJECTED`), an integer `score`, readable `reasons`, and the effective threshold.
All scoring constants live together in that module. This is an explainable
heuristic, not proof that an email describes a real assignment; imports still
require review.

| Evidence | Score |
| --- | --- |
| Assignment, homework, coursework, or structured academic task announcement | +6 |
| Specific phrase, e.g. assignment posted, assignment deadline, quiz uploaded, graded task | +8 instead of +6 |
| Assignment-specific evidence appears in subject | +1 |
| Academic task linked to uploaded/posted/available/due/deadline/submit/new | +2 |
| Numbered academic task, e.g. Assignment 3 | +2 |
| Course/class/lecture/semester/instructor/teacher/faculty/section/submission portal/marks/grade/lab | +1 each, maximum +3 |
| Configured trusted academic sender/domain | +2 |
| Fee/payment/invoice/tuition/bank/transaction/billing/subscription/promotion/sale/discount/newsletter/unsubscribe/shipping/order/reviews/marketing/offer/purchase terminology | -5 per distinct category in subject, otherwise -1 in body; total penalty capped at -15 |

Each signal/category is counted once; repeating keywords does not increase its
score. Specific phrases replace the generic strong-word score rather than stacking
all overlapping phrases. The lower body-negative weight permits legitimate LMS
messages with newsletter/unsubscribe footers when the academic evidence is strong.

Acceptance requires **both assignment-specific evidence and a passing score**.
Weak words, generic deadlines, configurable keyword hints, and trusted sender status
can never satisfy the evidence requirement by themselves. No sender/domain is
trusted by default. Unknown senders need `ASSIGNMENT_SCORE_THRESHOLD + 2` (default
8); trusted senders use the base threshold (default 6) and receive the trust bonus.
No guessed university domains are built in.

Trust matches parsed addresses, not display names. Exact domain rules include
subdomains with a proper dot boundary; `example.edu.evil.test` cannot match
`example.edu`. Shell-style patterns are supported. These are confidence hints,
not email-authenticity checks. Existing optional `GMAIL_*` sender allowlists still
apply and cannot be overridden by trust.

`Fee Payment Due Date Passed` scores -10 and is rejected. The fee-warning,
notes/reviews marketing, tuition-payment, project promotion, and course newsletter
examples are rejected. `Compiler Construction Assignment 2 Uploaded`,
`AI Lab Assignment Deadline: September 22`, and `Assignment 3 is due tomorrow`
have assignment-specific evidence and pass the default unknown-sender threshold.

### Safe dry-run and verbose output

```bash
python -m app.main gmail-scan --dry-run
python -m app.main gmail-scan --dry-run --verbose
```

Dry-run does not instantiate, read, migrate, or write SQLite, and does not send
notifications, including previously pending notifications. It evaluates recent
search-matching candidates, even previously imported/ignored ones, using the
same metadata prefilter as normal scanning. Its accepted count describes classifications, not how many new records a
normal scan would create. OAuth may still refresh its token file to permit the
read-only API calls; dry-run is not an offline mode.

The default dry-run shows classification, score, and a bounded subject for each
likely candidate. `--verbose` also shows sender and reasons. Candidate reporting
includes positive, weak, negative, or configured keyword signals, so rejected fee
and promotional messages are visible. Unrelated emails without such signals are
counted but not individually printed. Headers are truncated and terminal control
characters escaped. Bodies, token contents, credentials, and raw API error
messages are never printed. Verbose reporting also works with normal scans.

Normal scans retain the existing processed-ID deduplication and do not reevaluate
processed messages. This change does not remove existing false-positive records;
review and delete those locally with the existing `delete` command if desired.
The five-minute reminder checker and 8 PM daily summary are unchanged. No Gmail
background timer has been added.

### Safer API error diagnostics

Errors report the HTTP status, numeric Google error code when present, recognized
Google reason/status codes, and a sanitized message ID when processing one message.
For example: `HTTP 403; Google code 403; Google reason/status domainPolicy`.
Unknown machine codes are redacted instead of echoing arbitrary API text. The app
never prints raw error bodies, request URLs, Authorization headers, or tokens.
A failed message remains unprocessed for retry and does not prevent the scanner
from processing subsequent messages. If listing a page itself fails, the scan
reports that error and stops further discovery, while processing IDs already found.

The parser follows Google's documented [HTTP and structured JSON error format](https://developers.google.com/workspace/gmail/api/guides/handle-errors).


## Efficient scanning and rate limits

The default assignment-specific query is:

```text
newer_than:7d -in:sent -in:drafts {"assignment" "assignments" "homework" "coursework" "quiz" "assessment" "graded task"}
```

Gmail applies searches before returning candidate IDs. Terms can match subject
or body. A second broad academic query also searches deadline, due, submission,
LMS, course, and class. These discovery matches are not assignment classifications. Both read and unread recent messages are included;
`GMAIL_UNREAD_ONLY=true` restricts the query without marking anything read.
Gmail supports these query filters through
[`messages.list`](https://developers.google.com/workspace/gmail/api/guides/filtering).

Stage A merges and deduplicates ID-only results from both queries (up to 100 per
page, bounded by the remaining global candidate limit), skips already processed
IDs in a normal scan, and fetches only
Subject/From/List-Unsubscribe headers, receipt time, and a bounded snippet with
`format=metadata` and a partial field mask. Duplicate IDs within a scan are skipped.

Stage B fetches `format=full` only when the subject/snippet mentions an academic
task, or an eligible non-financial/non-promotional message has academic context,
deadline/due/submission/LMS hints, or a trusted sender. Configured sender allowlists still apply. Metadata rejections
are explained as such in verbose output. The unchanged strict classifier makes
the final decision from the full body. Import and notification deduplication is
unchanged. Normal scans retain ignored-message markers; dry-runs write none.

This prefilter trades some recall for fewer body fetches: an unknown sender's
vague subject/snippet can be skipped even if assignment wording occurs deep in
the body. Configure known academic senders and inspect dry-run results. Broader
search terms alone do not bypass the metadata gate or strict classifier.

Metadata GETs are still API requests, and a relevant new email requires both a
metadata and a full GET. The reduction comes from searching fewer messages,
processed-ID deduplication, and skipping clearly irrelevant full fetches—not from
assuming metadata requests are quota-free. Requests run sequentially with bounded
pagination, avoiding parallel batch bursts. The default 100 ms pacing applies
between attempted full-message fetches, not before the first one. Retry waits
are additional.

### Retry policy

The application owns one retry loop; SDK-level retries are disabled to avoid
multiplying attempts. It retries HTTP 429, rateLimitExceeded,
userRateLimitExceeded, RESOURCE_EXHAUSTED, backendError, and transient HTTP
500/502/503/504. A bare PERMISSION_DENIED/domainPolicy/insufficientPermissions
403 is not retried as though it were a rate limit. Google documents why a 403
needs its structured reason examined in its
[error-handling guide](https://developers.google.com/workspace/gmail/api/guides/handle-errors).

There are four retries after the initial attempt by default (five attempts
maximum), with delays of 1, 2, 4, and 8 seconds plus 0–0.5 seconds of jitter.
The exponential component is capped at 30 seconds when a larger retry count is
configured. `Retry-After` is honored as either seconds or an HTTP date, using the
larger of the server wait and local backoff. If the server asks for more than
60 seconds, the request fails for this scan without an early retry; try a later
manual scan. Retries are never infinite, and permanent message failures do not
prevent processing subsequent candidates. Listing failures end enumeration but
still return the partial summary and a nonzero CLI exit code.

Verbose output prints messages such as `Rate limit hit. Retrying in 2.4 seconds...`
without credentials, token contents, bodies, or raw error responses.

### Scan summary

The CLI reports candidate IDs found, metadata fetched, full messages fetched,
assignments classified, rejected, skipped, retries performed, exhausted/deferred
rate-limit failures, and other errors. Candidate counts cover only the retrieved,
bounded scan, not the entire mailbox. Successful retries are counted as retries,
not terminal errors. Full-fetch counts represent successful API responses;
failed attempts are reflected in retry/error counters. The existing new-record
and notification counts are retained for normal scans.

Recommended next validation:

```bash
GMAIL_MAX_MESSAGES=100 GMAIL_FETCH_DELAY_MS=100 python -m app.main gmail-scan --dry-run --verbose
```

No systemd files or timers change, and no automatic Gmail scanning is enabled.


## Broad discovery with a strict final classifier

Candidate discovery and final assignment classification are independent. The
strict classifier, its weights, and its thresholds are unchanged. By default,
the scanner executes these queries sequentially, while capacity remains:

```text
newer_than:7d -in:sent -in:drafts {"assignment" "assignments" "homework" "coursework" "quiz" "assessment" "graded task"}
newer_than:7d -in:sent -in:drafts {"deadline" "due" "submission" "LMS" "course" "class"}
```

IDs are merged in first-seen order and deduplicated before metadata retrieval.
The default limit is **100 unique IDs for the entire scan**, not 100 assignments
or 100 IDs per query. Queries and pages are bounded, including duplicate-heavy
and empty-page responses. Hitting the global limit stops further discovery and
produces the existing truncation notice. Counts in debug output describe IDs
actually read within these bounds, not total matches in the mailbox.

If both queries finish without errors and return zero IDs, a fallback query runs:

```text
newer_than:7d -in:sent -in:drafts
```

Fallback inspects up to **25 recent messages**, limited further by the global
maximum. `GMAIL_FALLBACK_MESSAGES=0` disables it; values up to 50 are allowed.
It is not invoked merely because discovered candidates were later rejected, or
because listing failed. An optional `GMAIL_UNREAD_ONLY=true` setting adds
`is:unread` to all queries, including fallback.

Every unique candidate still goes through metadata first: ID, subject, sender,
receipt time, and snippet if available. Financial/promotional messages lacking
assignment-specific hints and unrelated personal messages are rejected without
full-body retrieval. Broader academic/deadline/LMS hints permit full retrieval,
but only the unchanged strict classifier can accept the message as an assignment.
A generic course deadline is therefore discoverable but still rejected if its
full content has no strong assignment evidence. No blind full-mailbox fetch occurs.

Run:

```bash
python -m app.main gmail-scan --dry-run --verbose --discovery-debug
```

`--discovery-debug` prints the queries actually executed, returned and newly added
ID counts per query, page counts, total unique IDs, duplicates merged, fallback
usage, and metadata candidate count (the number eligible for full retrieval).
It does not print message bodies or OAuth data. `--verbose` retains the existing
bounded subject/sender/classification explanations. Debugging by itself does not
change write behavior; include `--dry-run` for no SQLite access or notifications.
Token refresh may still occur to authorize read-only Gmail requests.

Existing processed-message deduplication applies to normal scans; dry-run still
reevaluates discovered processed IDs without opening SQLite. No classification
threshold, reminder rule, OAuth scope, or timer has changed, and automatic Gmail
scanning remains disabled.

## Assignment alert email

See [Gmail notification setup](EMAIL_NOTIFICATIONS.md). Previously saved read-only
tokens are rejected with an explicit reauthorization instruction; scans never
silently open a consent browser or broaden a token. After reviewing the new send
permission, run `python -m app.main gmail-auth --force`.
