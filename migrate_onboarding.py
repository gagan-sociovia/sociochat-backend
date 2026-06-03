"""Add Tech Provider onboarding columns to whatsapp_accounts."""
import os
from dotenv import load_dotenv

load_dotenv()

from app import app
from models import db

MIGRATIONS = [
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS onboarding_status VARCHAR(32) NOT NULL DEFAULT 'PENDING'",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS onboarding_error TEXT",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS last_validation_at TIMESTAMP",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS app_subscribed BOOLEAN NOT NULL DEFAULT FALSE",
]

# SQLite fallback (no IF NOT EXISTS on older SQLite — handled in app via create_all)
SQLITE_MIGRATIONS = [
    "ALTER TABLE whatsapp_accounts ADD COLUMN onboarding_status VARCHAR(32) DEFAULT 'PENDING'",
    "ALTER TABLE whatsapp_accounts ADD COLUMN onboarding_error TEXT",
    "ALTER TABLE whatsapp_accounts ADD COLUMN last_validation_at TIMESTAMP",
    "ALTER TABLE whatsapp_accounts ADD COLUMN app_subscribed BOOLEAN DEFAULT 0",
]

with app.app_context():
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    is_sqlite = uri.startswith("sqlite")
    migrations = SQLITE_MIGRATIONS if is_sqlite else MIGRATIONS

    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
            print(f"OK: {sql[:60]}...")
        except Exception as exc:
            db.session.rollback()
            print(f"SKIP (may exist): {exc}")

    # Backfill: existing active accounts -> ACTIVE onboarding status
    try:
        db.session.execute(
            db.text(
                "UPDATE whatsapp_accounts SET onboarding_status = 'ACTIVE' "
                "WHERE is_active = TRUE AND (onboarding_status IS NULL OR onboarding_status = 'PENDING')"
            )
        )
        db.session.execute(
            db.text(
                "UPDATE whatsapp_accounts SET app_subscribed = TRUE "
                "WHERE is_active = TRUE AND onboarding_status = 'ACTIVE'"
            )
        )
        db.session.commit()
        print("Backfilled ACTIVE status for existing connected accounts")
    except Exception as exc:
        db.session.rollback()
        print(f"Backfill note: {exc}")

    print("Onboarding migration complete.")
