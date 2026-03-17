"""Add coexistence columns to whatsapp_accounts table."""
from app import app, db
from sqlalchemy import text

alter_statements = [
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS is_coexistence BOOLEAN DEFAULT FALSE",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS meta_business_id VARCHAR(100)",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS mps_limit INTEGER DEFAULT 80",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS sync_status VARCHAR(20) DEFAULT 'idle'",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS last_echo_at TIMESTAMP",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS last_mobile_activity_at TIMESTAMP",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS history_sync_completed BOOLEAN DEFAULT FALSE",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS history_sync_progress INTEGER DEFAULT 0",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS coexistence_paired_at TIMESTAMP",
    "ALTER TABLE whatsapp_accounts ADD COLUMN IF NOT EXISTS device_inactive_alert_sent BOOLEAN DEFAULT FALSE",
]

with app.app_context():
    for stmt in alter_statements:
        try:
            db.session.execute(text(stmt))
            col_name = stmt.split("IF NOT EXISTS ")[1].split()[0]
            print(f"OK: {col_name}")
        except Exception as e:
            print(f"SKIP: {e}")
    db.session.commit()
    print("Migration complete!")
