"""Run with python -m examples.reminder_demo; temporary synthetic data only."""

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from app.database import Database
from app.models import Assignment, LOCAL_TIMEZONE, Status
from app.notifier import Notifier
from app.scheduler import check_reminders, daily_summary


def main() -> None:
    now = datetime(2030, 9, 16, 20, tzinfo=LOCAL_TIMEZONE)
    with TemporaryDirectory(prefix="assignment-demo-") as directory:
        database = Database(Path(directory) / "assignments.db")
        for title, delta, status in (
            ("Outside reminder dates", timedelta(days=3), Status.NEW),
            ("Tomorrow task", timedelta(days=1), Status.REVIEWED),
            ("Due today task", timedelta(hours=1), Status.IN_PROGRESS),
            ("Later task", timedelta(days=5), Status.READY_TO_SUBMIT),
            ("Past date task", timedelta(days=-1), Status.NEW),
            ("Already submitted", timedelta(hours=-1), Status.SUBMITTED),
        ):
            database.create_assignment(Assignment(course="Synthetic Course", title=title,
                                                   deadline=now + delta, status=status))
        notifier = Mock(spec=Notifier)
        notifier.send.side_effect = lambda title, body, **kwargs: print(f"[MOCK NOTIFICATION] {title}\n{body}")
        print("Synthetic demo: temporary database, mocked notifications, fixed Karachi clock.")
        print(f"First check: {check_reminders(database, notifier, now)}")
        print(f"Repeat check: {check_reminders(database, notifier, now)}")
        print(daily_summary(database, now))


if __name__ == "__main__":
    main()
