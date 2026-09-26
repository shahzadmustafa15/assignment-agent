from app.config import load_settings
from app.database import Database

db = Database(load_settings().database_path)

with db._connect() as connection:
    connection.execute("BEGIN IMMEDIATE")

    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(notification_events)"
        )
    }

    if "previous_deadline" not in columns:
        connection.execute(
            """
            ALTER TABLE notification_events
            ADD COLUMN previous_deadline TEXT NOT NULL DEFAULT ''
            """
        )

        print("Added previous_deadline.")
    else:
        print("previous_deadline already exists.")

    problems = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print("Foreign-key problems:", len(problems))
