# Bahria LMS current-course scanning

Use the existing virtual environment and saved session/private scan target:

```bash
source .venv/bin/activate
python -m app.main lms-courses --dry-run --verbose
python -m app.main lms-scan --dry-run --verbose
```

Discovery reads the Dashboard Courses Registered table. The newest offered
Spring/Summer/Fall term is the current-term policy because the observed portal
has no explicit current flag. Only registered rows for that term are included;
historical semesters are not traversed. Navigation follows actual visible links
and semester/course dropdown handlers without constructing course URLs.
Ambiguous same-name sections are skipped rather than guessed.

`lms-courses` is always read-only and lists all discovered current courses.
`lms-scan` scans eligible courses with the existing assignment parser. Verbose
output includes per-course counts, skipped reasons, evidence and aggregate totals.
Course navigation failures are isolated; authentication expiry stops requests.

Dry-run compares through a read-only SQLite connection: no database creation,
imports, notifications, or saved session/auth-notice updates. It acquires the
existing scan lock. New counts are preview candidates, including review records.

## Filters

Export optional comma-separated exact names or exposed codes:

```bash
export LMS_COURSE_INCLUDE="Artificial Intelligence Lab,Introduction to Data Science Lab"
export LMS_COURSE_EXCLUDE="Artificial Intelligence Lab"
python -m app.main lms-scan --dry-run --verbose
```

Names are case/whitespace normalized; exclusion wins. Empty include means all
current courses. The observed portal exposes names, not codes. The app does not
automatically load `.env`. Discovery lists all courses; filters apply to scanning.

## Compatibility and deduplication

`lms-scan --single-course --dry-run --verbose` preserves the original interactive
flow. `--configure-background` retains its existing setup behavior. Deadline
conflicts, actual/conditional deadline labels, submission evidence, marks,
comments and review reasons retain the existing parser semantics.

Existing identifiers and exact known-course matching are retained. Unknown-course
records require matching title, number, known deadline and term plus either a
matching recorded course ID or uniqueness across a complete, unfiltered,
error-free current-course survey. Ambiguous records remain for review without
duplicate insertion or cross-course merging. Notifications are processed once
per overall import; metadata updates do not create new-assignment notifications.

## Existing schedule and limits

Manual and background scans default to all current registered courses. The
previous private `multi_course_enabled` review gate is no longer used.
`--single-course` selects the original interactive flow manually or the saved
course target in background mode. The existing 30-minute service/timer needs
no unit modification or restart. Gmail, reminders, notifications and authentication
cooldown policies remain unchanged.

Coverage is the fetched assignment page per course. Pagination and Action/detail
navigation remain unverified. A manual scan without `--dry-run` imports records;
review the preview before choosing to run it.
