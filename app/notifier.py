"""Desktop notification boundary; importing this module never sends anything."""

import json
import os
from html import escape
import shutil
import subprocess
from typing import Protocol


class NotificationError(RuntimeError):
    """The desktop notification could not be handed to notify-send."""


class Notifier(Protocol):
    def send(self, title: str, body: str, *, urgent: bool = False) -> None:
        """Send a notification, raising NotificationError on failure."""


class DesktopNotifier:
    """Use the logged-in Ubuntu user's notification service via notify-send."""

    def send(self, title: str, body: str, *, urgent: bool = False) -> None:
        if os.environ.get("ASSIGNMENT_NOTIFICATION_DESTINATION") == "journal":
            print(json.dumps({"event": "local_notification", "title": title,
                              "body": body, "urgent": urgent}, ensure_ascii=True), flush=True)
            return
        executable = shutil.which("notify-send")
        if executable is None:
            raise NotificationError("notify-send is unavailable; install Ubuntu's libnotify-bin package")
        try:
            subprocess.run(
                [executable, "--app-name=Assignment Agent",
                 "--urgency=" + ("critical" if urgent else "normal"),
                 "--", title, escape(body)],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotificationError(
                "Desktop notification failed. Check your logged-in desktop session "
                "and D-Bus availability; no reminder was recorded as sent."
            ) from exc
