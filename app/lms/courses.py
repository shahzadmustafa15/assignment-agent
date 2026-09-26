"""Current-course discovery from the verified Bahria registration table and selects.

Dashboard course rows are plain text, not links. Navigation uses the real
Assignments anchor plus semesterId/courseId select change handlers; no URL is
constructed from course IDs. Only course/term DOM is inspected, never profiles.
"""
from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

from app.lms.base import LMSError
from app.lms.background import AuthExpired, check_auth, event
from app.lms.extraction import read_assignment_table
from app.lms.parser import parse_assignment_table, public_link, safe_text
from app.models import normalized

REGISTERED_HEADERS = ('Sr.No.', 'Course Title', 'Class', 'Semester')

COURSE_SCRIPT = r'''() => {
 const clean = s => (s || '').replace(/\s+/g, ' ').trim();
 const visible = e => !!e.getClientRects().length;
 const select = id => {
   const e = document.getElementById(id);
   if (!e || e.tagName !== 'SELECT' || !visible(e)) return null;
   return {value:e.value, handler:e.getAttribute('onchange'),
     options:Array.from(e.options).map(o=>({text:clean(o.textContent),value:o.value,
       selected:o.selected,disabled:o.disabled}))};
 };
 return {semester:select('semesterId'), course:select('courseId'),
   registrations:Array.from(document.querySelectorAll('table')).filter(visible).map(t=>({
     headers:Array.from(t.querySelectorAll('th')).filter(h=>h.closest('table')===t).map(h=>clean(h.innerText)),
     rows:Array.from(t.rows).filter(r=>r.closest('table')===t && visible(r) && r.querySelector('td'))
       .map(r=>Array.from(r.cells).map(c=>({text:clean(c.innerText),colspan:c.colSpan})))
   })).filter(t=>t.headers.includes('Course Title'))};
}'''


@dataclass(frozen=True)
class Course:
    name: str
    section: str
    semester: str
    semester_value: str
    selector_value: str = ''
    code: str = ''
    course_url: str | None = None
    assignments_url: str | None = None

    @property
    def stable_id(self):
        if not self.selector_value:
            return None
        data = json.dumps([self.semester_value, self.selector_value])
        return 'bahria:course:' + hashlib.sha256(data.encode()).hexdigest()

    def metadata(self):
        return {'id': self.stable_id, 'name': self.name, 'code': self.code,
                'section': self.section, 'semester': self.semester}


class NoAssignmentsSection(LMSError):
    pass


def navigation_link(page, label):
    """Visible anchor text is authoritative; icons alter these accessible names."""
    matches = page.locator('a').filter(has_text=re.compile(r'^\s*' + re.escape(label) + r'\s*$'))
    urls = set()
    for index in range(matches.count()):
        link = matches.nth(index)
        if not link.is_visible() or link.get_attribute('onclick'):
            continue
        url = public_link(link.get_attribute('href') or '', page.url)
        if url and urlsplit(url).netloc == urlsplit(page.url).netloc:
            urls.add(url)
    if len(urls) > 1:
        raise LMSError('Ambiguous course navigation links.')
    return next(iter(urls), None)


def navigate(page, url):
    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
    check_auth(page, response)
    if urlsplit(page.url).netloc != urlsplit(url).netloc:
        raise AuthExpired('LMS navigation requires login.')


def select_value(page, identifier, value, expected_handler):
    snapshot = page.evaluate(COURSE_SCRIPT)
    selected = snapshot['semester' if identifier == 'semesterId' else 'course']
    if not selected or selected['handler'] != expected_handler:
        raise LMSError('Course selector structure changed; navigation refused.')
    if value not in {o['value'] for o in selected['options'] if not o['disabled']}:
        raise LMSError('Selected course or semester is no longer available.')
    if selected['value'] != value:
        origin = urlsplit(page.url).netloc
        with page.expect_navigation(wait_until='domcontentloaded', timeout=15000) as navigation:
            page.locator('select#' + identifier).select_option(value)
        check_auth(page, navigation.value)
        if urlsplit(page.url).netloc != origin:
            raise AuthExpired('Course navigation redirected outside LMS.')
        selected = page.evaluate(COURSE_SCRIPT)['semester' if identifier == 'semesterId' else 'course']
        if not selected or selected['value'] != value:
            raise LMSError('Course selection did not take effect.')


def current_semester(snapshot):
    """Latest offered term only, checked against the registered-course rows.

    The observed portal offers Spring/Summer/Fall terms newest first, without a
    separate current flag. Rank labels explicitly rather than relying on order or
    a possibly historical selection. Never walk the historical option list.
    """
    options = (snapshot.get('semester') or {}).get('options', [])
    ranked = []
    for option in options:
        if option['disabled'] or not option['value']:
            continue
        match = re.fullmatch(r'(Spring|Summer|Fall)-(\d{4})', option['text'], re.I)
        if not match:
            raise LMSError('Unrecognized semester labels; current term cannot be verified.')
        ranked.append(((int(match[2]), {'spring': 1, 'summer': 2, 'fall': 3}[match[1].lower()]), option))
    if not ranked:
        raise LMSError('No verified current semester.')
    newest = max(key for key, _ in ranked)
    choices = [option for key, option in ranked if key == newest]
    if len(choices) != 1:
        raise LMSError('Ambiguous current semester.')
    return choices[0]


def registered_rows(snapshot, semester):
    tables = snapshot['registrations']
    if len(tables) != 1 or tuple(tables[0]['headers']) != REGISTERED_HEADERS:
        raise LMSError('Courses Registered table structure changed.')
    courses = []
    seen = set()
    for row in tables[0]['rows']:
        if len(row) == 1 and row[0]['colspan'] == 4 and re.search(r'\b(no|not|empty)\b', row[0]['text'], re.I):
            continue
        if len(row) != 4 or any(cell['colspan'] != 1 for cell in row):
            raise LMSError('Malformed registered-course row.')
        _, name, section, term = [cell['text'] for cell in row]
        if normalized(term) != normalized(semester['text']):
            continue
        if not name or not section:
            raise LMSError('Incomplete registered-course identity.')
        key = (normalized(name), normalized(section), normalized(term))
        if key not in seen:
            courses.append(Course(name, section, term, semester['value']))
            seen.add(key)
    return courses


def discover_courses(context, target):
    page = context.new_page()
    try:
        navigate(page, target['url'])
        dashboard = navigation_link(page, 'Dashboard')
        if not dashboard:
            raise LMSError('No verified Dashboard link on the configured LMS page.')
        navigate(page, dashboard)
        semester = current_semester(page.evaluate(COURSE_SCRIPT))
        select_value(page, 'semesterId', semester['value'], 'SelectSemester()')
        courses = registered_rows(page.evaluate(COURSE_SCRIPT), semester)
        assignments = navigation_link(page, 'Assignments')
        if not assignments or not courses:
            return courses
        navigate(page, assignments)
        select_value(page, 'semesterId', semester['value'], 'SelectSemester()')
        select = page.evaluate(COURSE_SCRIPT).get('course')
        if not select or select['handler'] != 'GetCourses()':
            raise LMSError('No verified registered-course selector on Assignments page.')
        name_counts = Counter(normalized(course.name) for course in courses)
        found = []
        for course in courses:
            options = [o for o in select['options'] if o['value'] and not o['disabled']
                       and normalized(o['text']) == normalized(course.name)]
            if len(options) == 1 and name_counts[normalized(course.name)] == 1:
                found.append(replace(course, selector_value=options[0]['value'], assignments_url=assignments))
            else:
                # Do not attach an identically named section to an arbitrary option.
                found.append(course)
        return found
    finally:
        page.close()


def fetch_course_table(context, course):
    if not course.assignments_url or not course.selector_value:
        raise NoAssignmentsSection('No unambiguous assignment navigation for this registered course.')
    page = context.new_page()
    try:
        navigate(page, course.assignments_url)
        select_value(page, 'semesterId', course.semester_value, 'SelectSemester()')
        select_value(page, 'courseId', course.selector_value, 'GetCourses()')
        table = read_assignment_table(page)
        if normalized(table['course']) != normalized(course.name) or normalized(table['semester']) != normalized(course.semester):
            raise LMSError('Course context disagrees with the registered course.')
        table['course'] = course.name
        table['semester'] = course.semester
        table['course_context'] = course.metadata()
        return table
    finally:
        page.close()


def course_filter(course, include='', exclude=''):
    def terms(value):
        return {normalized(part) for part in value.split(',') if part.strip()}
    values = {normalized(course.name)}
    if course.code:
        values.add(normalized(course.code))
    return (not terms(include) or bool(values & terms(include))) and not bool(values & terms(exclude))


def assignment_signature(record):
    a, metadata = record.assignment, record.metadata
    return (normalized(a.title), normalized(metadata.get('assignment_number', '')),
            a.deadline.isoformat() if a.deadline else '', normalized(metadata.get('semester', '')))


def show_courses(courses, verbose):
    print(f'Discovered courses: {len(courses)}')
    for index, course in enumerate(courses, 1):
        if verbose:
            print(json.dumps({'course_number': index, 'course': safe_text(course.name),
                'code': safe_text(course.code) or 'Not exposed', 'section': safe_text(course.section),
                'semester': safe_text(course.semester), 'course_id': course.stable_id,
                'course_url': course.course_url or 'Not exposed; course selector navigation',
                'assignments_navigation_url': course.assignments_url}, ensure_ascii=True))
        else:
            print(f'{index}. {safe_text(course.name)} ({safe_text(course.semester)})')


def scan_courses(context, target, database_path, notifier, *, dry_run=False, verbose=False,
                 discover_only=False, include=None, exclude=None, notify=True, report_details=False,
                 student_id="shahzad"):
    from playwright.sync_api import Error
    from app.lms.scan import scan_records
    include = os.environ.get('LMS_COURSE_INCLUDE', '') if include is None else include
    exclude = os.environ.get('LMS_COURSE_EXCLUDE', '') if exclude is None else exclude
    courses = discover_courses(context, target)
    event('authentication_valid')
    show_courses(courses, verbose)
    result = dict(courses_discovered=len(courses), courses_scanned=0, courses_skipped=0,
                  parsed=0, new=0, existing=0, updated=0, review=0, submitted=0,
                  overdue=0, notified=0, errors=[], reports=[], courses=[], auth_expired=False)
    if discover_only:
        return result
    batches = []
    complete = True
    for index, course in enumerate(courses, 1):
        summary = {'course': course.name if report_details else safe_text(course.name), 'course_id': course.stable_id,
                   'semester': safe_text(course.semester), 'section': safe_text(course.section), 'scanned': False, 'assignments': 0, 'reason': ''}
        result['courses'].append(summary)
        if not course_filter(course, include, exclude):
            summary['reason'] = 'Filtered out'
            result['courses_skipped'] += 1
            complete = False
            continue
        try:
            table = fetch_course_table(context, course)
            records, errors = parse_assignment_table(table)
            for record in records:
                record.metadata['course_context'] = course.metadata()
            result['courses_scanned'] += 1
            summary.update(scanned=True, assignments=len(records), reason='Parsed' if records else 'No assignments')
            batches.append((records, errors, summary))
            if errors:
                complete = False
        except NoAssignmentsSection:
            summary['reason'] = 'No unambiguous Assignments section or course selector'
            result['courses_skipped'] += 1
            complete = False
        except AuthExpired:
            summary['reason'] = 'Authentication expired'
            result['auth_expired'] = True
            result['errors'].append(f'Course {index}: authentication expired; remaining courses not requested.')
            result['courses_skipped'] += len(courses) - index + 1
            for remaining in courses[index:]:
                result['courses'].append({'course': safe_text(remaining.name), 'scanned': False,
                                         'assignments': 0, 'reason': 'Skipped after authentication expired'})
            complete = False
            break
        except (LMSError, Error, ValueError, TypeError, KeyError):
            summary['reason'] = 'Course navigation or table validation failed'
            result['errors'].append(f'Course {index}: navigation or table validation failed; continued safely.')
            result['courses_skipped'] += 1
            complete = False
    signatures = Counter(assignment_signature(r) for records, _, _ in batches for r in records)
    # Import all successfully fetched courses in one batch: pending notifications
    # are attempted once per scan, not retried once for every subsequent course.
    records = [r for batch, _, _ in batches for r in batch]
    parse_errors = [f"{summary['course']}: {error}" for _, errors, summary in batches for error in errors]
    unique = {assignment_signature(r) for r in records
              if complete and signatures[assignment_signature(r)] == 1}
    scanned = scan_records(
        records,
        parse_errors,
        database_path,
        notifier,
        dry_run=dry_run,
        verbose=verbose,
        unknown_course_signatures=unique,
        notify=notify,
        report_details=report_details,
        student_id=student_id,
    )
    for key in ('parsed', 'new', 'existing', 'updated', 'review', 'submitted', 'overdue', 'notified'):
        result[key] += scanned[key]
    result['errors'].extend(scanned['errors'])
    result['reports'].extend(scanned['reports'])
    for summary in result['courses']:
        print(json.dumps({'event': 'course_result', **summary}, ensure_ascii=True))
    event('multi_course_result', **{k: result[k] for k in (
        'courses_discovered', 'courses_scanned', 'courses_skipped', 'parsed', 'new',
        'existing', 'updated', 'review', 'submitted', 'overdue', 'notified')}, errors=len(result['errors']))
    return result
