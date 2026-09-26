from app.config import load_settings
from app.database import Database


db = Database(load_settings().database_path)

columns_to_add = {
    "created_at": "TEXT",
    "recipient_email": "TEXT",
    "gmail_message_id": "TEXT",
    "error_type": "TEXT",
    "error_message": "TEXT",
}

with db._connect() as connection:
    connection.execute("BEGIN IMMEDIATE")

    existing = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(notification_events)"
        )
    }

    for name, column_type in columns_to_add.items():
        if name not in existing:
            connection.execute(
                f"""
                ALTER TABLE notification_events
                ADD COLUMN {name} {column_type}
                """
            )
            print("Added:", name)
        else:
            print("Already exists:", name)

    connection.execute(
        """
        UPDATE notification_events
        SET created_at = COALESCE(
            created_at,
            attempted_at,
            sent_at,
            datetime('now')
        )
        WHERE created_at IS NULL
        """
    )

    problems = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

print("Notification audit migration complete.")
print("Foreign-key problems:", len(problems))
