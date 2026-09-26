"""Manual LMS scans; dry-run reads SQLite through a read-only connection."""
import json
import sqlite3
from dataclasses import replace
from app.lms.parser import parse_assignment_table, safe_text, safe_url
from app.lms.storage import LMSStorage, readonly_connection, match_assignment
from app.models import LOCAL_TIMEZONE, Status, normalized


def scan_table(table, database_path, notifier, *, dry_run=False, verbose=False,
               student_id="shahzad"):
    records, errors = parse_assignment_table(table)
    return scan_records(
        records,
        errors,
        database_path,
        notifier,
        dry_run=dry_run,
        verbose=verbose,
        student_id=student_id,
    )


def scan_records(records, errors, database_path, notifier, *, dry_run=False, verbose=False,
                 unknown_course_signatures=None, notify=True, report_details=False,
                 student_id="shahzad"):
    errors = list(errors)
    created = existing = review = updated = submitted = overdue = 0
    reports = []
    store = None if dry_run or not records else LMSStorage(database_path)
    seen = set()
    for index, record in enumerate(records, 1):
        assignment = replace(
            record.assignment,
            student_id=student_id,
        )
        record = replace(
            record,
            assignment=assignment,
        )
        local_assignment = assignment
        signature = (normalized(assignment.title), normalized(record.metadata.get('assignment_number', '')),
                     assignment.deadline.isoformat() if assignment.deadline else '',
                     normalized(record.metadata.get('semester', '')))
        allow_unknown = signature in (unknown_course_signatures or set())
        try:
            if assignment.external_message_id in seen:
                existing += 1
                continue
            seen.add(assignment.external_message_id)
            if dry_run:
                with readonly_connection(database_path) as connection:
                    current = match_assignment(connection, assignment, metadata=record.metadata,
                                               allow_unknown_course=allow_unknown)
                is_new = current is None
            else:
                local_assignment, is_new, changed = store.import_record(record, include_updated=True,
                                                         allow_unknown_course=allow_unknown)
                updated += int(changed)
            submitted += int(assignment.status is Status.SUBMITTED)
            overdue += int(assignment.status is Status.OVERDUE)
            created += int(is_new)
            existing += int(not is_new)
            review += int(assignment.needs_review)
            classification = 'EXISTING' if not is_new else ('NEEDS_REVIEW' if assignment.needs_review else 'NEW')
            report = {'Course': safe_text(assignment.course), 'Title': safe_text(assignment.title),
                'Raw deadline': record.metadata['deadline_text'],
                'Raw deadline values': record.metadata['deadline_raw_values'],
                'Deadline sources': record.metadata['deadline_candidates'],
                'Deadline conflict': record.metadata['deadline_conflict'],
                'Deadline selection reason': record.metadata['deadline_selection_reason'],
                'Identity strategy': record.metadata['identity_strategy'],
                'Parsed deadline': assignment.deadline.astimezone(LOCAL_TIMEZONE).isoformat() if assignment.deadline else 'Unknown',
                'Action type': record.metadata['action']['type'],
                'Action URL/ID': record.metadata['action']['url'] or record.metadata['action']['id'] or 'None verified',
                'Submission evidence': record.metadata['submission_evidence'],
                'Local status': local_assignment.status.value,
                'Assignment number': safe_text(record.metadata.get('assignment_number', '')),
                'Returned comments': safe_text(record.metadata.get('returned_comments', '')),
                'Course context': record.metadata.get('course_context', {}),
                'Observed submitted': assignment.status is Status.SUBMITTED,
                'Submission status': safe_text(record.metadata['added_submission']) or 'Unknown',
                'Marks': safe_text(record.metadata['marks']) or 'Unknown',
                'LMS URL': safe_url(assignment.lms_url) if assignment.lms_url else 'Unverified / unavailable',
                'External ID': assignment.external_message_id, 'Classification': classification,
                'Would create' if dry_run else 'Created': is_new,
                'Needs review': local_assignment.needs_review, 'Reasons': record.metadata['review_reasons']}
            if report_details:
                from app.lms.parser import public_link
                report.update({'Course': assignment.course, 'Title': assignment.title,
                               'Assignment number': record.metadata.get('assignment_number', ''),
                               'Returned comments': record.metadata.get('returned_comments', ''),
                               'Marks': record.metadata.get('marks', '') or 'Unknown',
                               'LMS URL': (public_link(local_assignment.lms_url, local_assignment.lms_url)
                                           if local_assignment.lms_url else None) or 'Unverified / unavailable'})
            reports.append(report)
            if verbose:
                print(json.dumps(report, indent=2, ensure_ascii=True))
        except ValueError:
            errors.append(f'Assignment row {index}: ambiguous identity or changed deadline; manual review required. No insert.')
        except (sqlite3.Error, OSError):
            errors.append(f'Assignment row {index}: database comparison/import failed; row left for review.')
    notified = 0
    if not dry_run and store is not None and notify:
        def send(assignment):
            deadline = assignment.deadline.astimezone(LOCAL_TIMEZONE).strftime('%b %d, %Y %I:%M %p') if assignment.deadline else 'Unknown — review required'
            notifier.send('New Assignment Detected', f'{assignment.course} — {assignment.title}\nDue: {deadline}')
        notified, notification_errors = store.notify_pending(
            send,
            student_id=student_id,
        )
        errors.extend(notification_errors)
        from app.email_notifications import deliver_pending
        deliver_pending(store.database, event_types={'NEW_ASSIGNMENT'})
    print(f"{'Dry-run' if dry_run else 'LMS scan'}: {len(records)} parsed; {created} new; {existing} existing; "
          f'{updated} updated; {review} need review; {notified} notified; {len(errors)} errors.')
    if dry_run:
        print('No SQLite writes or notifications. New counts include assignments needing review.')
    if not records and not errors:
        print('No assignment rows on the fetched course pages.')
    print('Coverage: fetched assignment page per scanned course only; pagination and Action/detail navigation are not yet verified.')
    for error in errors:
        print(error)
    return {'submitted': submitted, 'overdue': overdue, 'parsed': len(records), 'updated': updated, 'new': created, 'existing': existing, 'review': review, 'notified': notified, 'errors': errors, 'reports': reports}
