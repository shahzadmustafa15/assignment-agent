# Manual Bahria LMS assignment table scanning

## Verified structure and remaining inspection

The authenticated LMS is opened in a new tab from CMS **Go To LMS**. The actual
Assignments page contains a native table with these headers:

- Assign. No.
- Title
- Assignment (Solution File) Remarks
- Added Submission
- Marks Obtained
- Returned Submission (Comments)
- Action
- Deadline

Course and semester selectors were observed with IDs `courseId` and `semesterId`.
The initial empty table contained a single cell spanning eight columns. Subsequent
populated-row observations and deadline labels are documented below. No assignment
detail page or stable portal assignment ID has been verified. Action controls are
never clicked by the scanner.

## Commands

```bash
python -m app.main lms-scan --dry-run --verbose
python -m app.main lms-scan
```

These commands reuse saved CMS state, open a visible browser, and wait for you to
navigate to a course's Assignments page. Select the LMS tab when prompted and
confirm its origin if different from CMS. Only that selected course/current page
is scanned. The program does not automatically change dropdowns or paginate.
The default scan remains interactive. Optional headless scanning and the staged
30-minute user timer are documented in [LMS automation](LMS_AUTOMATION.md); timer
installation requires explicit approval. There is no submission automation.

## Parsing and review

Column headings determine cell meanings, even if columns are reordered. Missing
or changed headings stop scanning. Malformed rows produce errors while subsequent
rows continue. Empty-table messages with a spanning cell are recognized; unexpected
spanning rows are errors. The selected course supplies course context; unknown
context is stored as `Unknown course`. Only explicit, unambiguous dates **and times**
produce deadlines; unzoned times use Asia/Karachi. Date-only deadlines remain unknown.

Number, title, remarks, added submission, marks, returned comments, safe links,
Action labels and raw deadline text are retained. Submission maps to SUBMITTED
only for explicit affirmative status text, never merely a filename, marks or a
Submit action. Existing SUBMITTED and local work statuses are preserved. Past
known deadlines can set new records OVERDUE.

Review is based on unresolved fields and evidence, as described below; a fallback
ID alone does not require review. No teacher, upload date, description, detail URL, attachment
role or portal-specific ID is invented. Safe hyperlinks are retained as metadata,
but are not treated as verified assignment-detail URLs or clicked.

## Local storage and duplicate handling

An additive `lms_records` table stores JSON metadata, LMS external aliases,
assignment foreign keys, observation times, and pending notification state. Existing
assignment/reminder tables are preserved. Deleting an assignment cascades to its LMS
metadata. New assignments have source `lms`. Matching Gmail records retain their
original Gmail identity and description, while gaining LMS provenance and metadata.

Confident-deadline fallback IDs hash normalized course/title and the deadline in
Asia/Karachi. Unresolved-deadline IDs instead hash course/title/assignment number/semester
with a separate namespace; no ambiguous timestamp or table row position is used. Exact normalized course,
title and a known matching deadline permit cross-source matching. Similar titles
are not merged. A changed deadline with the same course/title and no verified stable
ID is rejected for manual review, rather than creating a duplicate or silently
merging potentially different tasks. A future verified stable ID can support safe
reschedule updates. Identity conflicts never overwrite another record.

Normal imports and metadata updates are transactional. Only newly created,
unsubmitted assignments queue desktop notifications. Failed notifications remain
pending for a later nonempty normal scan. Notifications are serialized and recorded
once; a process crash after sending but before recording can still repeat delivery.
No new-assignment notification is sent when LMS merely enriches an existing record.

Dry-run does not construct Database/LMSStorage, initialize tables, write records,
save browser state, or send notifications. If a database exists, comparison uses
SQLite `mode=ro` plus `query_only`; an absent database remains absent. Verbose output
quotes/redacts text and omits URL paths/queries, and displays NEW/EXISTING/NEEDS_REVIEW
with explicit new-record counts. The database migration runs only on a nonempty
normal scan. Tests use synthetic snapshots and temporary databases.

## Next required validation

Inspect at least one actual populated assignment row and, only after verifying a
read-only detail link, its details page. This is necessary before claiming complete
Stage 2 support for assignment IDs, description, teacher, uploaded dates,
attachments, pagination, or automatic course traversal.

## Populated-row observations and parsing update

Two populated Data Science Lab rows were inspected. Exact raw deadline strings:

```text
9 September 2026 - 11:55 pm
9 September 2026-09:00 am
```

Those two lines occupy one Deadline cell; without verified revision semantics,
that record retains both raw lines and has an unknown deadline requiring review.
The second row has:

```text
16 September 2026-09:00 am
```

The verified `day full-month year - hour:minute am/pm` format now parses with
Asia/Karachi timezone. Invalid dates/times and differing multiline dates remain
unknown. No date-only midnight or inferred year is introduced.

Both inspected Action cells contain only `Deadline Exceeded`; there are no links,
buttons, forms or assignment IDs there. No Action detail page is available to
inspect. A download link is not promoted to a detail URL. Description, instructor,
uploaded date and submission instructions were not established by this inspection.

The assignment-file column contains `/Student/Download.php?k=...` links. The
observed envelope includes a category and filename. `Assignment` identifies an
instruction attachment; `AssignmentSubmissions` in **Added Submission** identifies
a submission file. The parser validates this shape and same-origin endpoint
without requesting downloads. `Submission` alone, arbitrary filename text or an
instruction attachment is insufficient for SUBMITTED. `No Submission` remains
negative evidence; contradictory file evidence requires review. Explicit affirmative
submission status also remains supported.

Opaque download keys are neither logged nor stored in LMS metadata. Only validated
attachment filenames/category and the endpoint without its query are retained.
The envelope's numeric fields and dated download URL have not been verified as
assignment IDs and are not used for identity. The course/title/deadline hash
remains the fallback, with `needs_review=true`. No guessed detail selectors or
background scanning were added.

Verbose dry-run now includes raw and parsed deadline, Action type and verified
URL/ID (if any), external ID, submission evidence, local status and review reasons.
Existing Gmail, reminder and timer behavior is unchanged.

## Deadline source resolution

A subsequent DOM inspection established both Lab 01 timestamps as visible `small`
elements, not hidden/mobile duplicates:

- `title="Extended only for missing"`, `class="label label-warning"`: 9 September 2026 - 11:55 pm.
- `title="Actual"`, `class="label label-info"`: 9 September 2026-09:00 am.

Lab 02 also has `title="Actual"`. Selection uses the explicit semantic label,
never DOM order, class name or color. The actual Lab 01 deadline is therefore
09:00 Asia/Karachi; its conditional extension is retained separately and still
requires review of applicability. It is not silently treated as the main deadline.

Metadata now includes raw values, text-node element/title/ARIA context, visibility,
parsed candidates, conflict flag, selection reason and conditional-extension flag.
Hidden candidates remain in the evidence but cannot override visible dates. A unique
Actual/Deadline-labeled value may resolve a conflict; unlabeled conflicting values,
multiple different labeled values, or invalid explicitly-labeled dates remain unknown.

Confident deadlines retain the existing course/title/deadline hash. Unresolved
ones now use a namespaced hash of normalized course/title/assignment number/semester,
excluding all ambiguous raw dates, current status, marks and row position. A fallback
ID alone is no longer a reason for review. Missing/ambiguous deadline, unknown course,
uncertain submission, unverified Action structure or a conditional extension still
warrant review. Lab 02's reliable course/title, labeled deadline, and submission-file
evidence now allow `needs_review=false`. Normal LMS refreshes can clear old default
review flags; Gmail-origin review flags remain preserved.

Previously stored ambiguous records whose identity changes are still handled
conservatively: an exact course/title conflict is reported for manual review, not
silently duplicated or merged. No existing records are migrated by a dry-run.

## Follow-up verification (20 September 2026)

A conditional-extension timestamp alone cannot establish the actual deadline. Both
raw values and their labels remain preserved; conflicting explicit deadline labels
remain unresolved. Clearing a review flag now also clears its stored review reasons,
while missing optional metadata such as marks remains preserved.

The initial live dry-run stopped because the saved CMS session required
reauthentication. After the user logged in again, the real verbose dry-run completed
successfully and independently confirmed the deadline labels above. Both Lab 01
values are visible text in separate `small` elements within the Deadline cell;
their `title` attributes are "Extended only for missing" and "Actual". These are
semantically different timestamps, not hidden or desktop/mobile duplicates.

Live results:

- Lab 01: actual deadline `2026-09-09T09:00:00+05:00`, status `OVERDUE`,
  `deadline_conflict=true`, `needs_review=true` solely because conditional-extension
  applicability remains unresolved. Both raw values and their DOM context survive.
- Lab 02: actual deadline `2026-09-16T09:00:00+05:00`, status `SUBMITTED` from a
  verified submission-file link, `deadline_conflict=false`, `needs_review=false`.
- Both Action cells remain plain text; no assignment detail URL or portal ID is
  verified. Both records use the existing confident-deadline fallback hash.
- Summary: 2 parsed, 2 would be new, 0 existing, 1 needs review, 0 notified, 0 errors.
  No SQLite writes or notifications occurred. Coverage remains the selected course
  and page only.

Validation: `python -m compileall app` succeeded and `pytest` passed all 307 tests
using the project virtual environment. Reminder policy and background-scan settings
were unchanged.
