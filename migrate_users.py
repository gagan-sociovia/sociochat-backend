"""Migrate users table to add missing columns."""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

db_uri = os.getenv("SQLALCHEMY_DATABASE_URI", "postgresql://postgres:StrongPasswordHere@34.47.128.166:5432/sociochat")
print(f"Connecting to: {db_uri[:50]}...")

conn = psycopg2.connect(db_uri)
conn.autocommit = True
cur = conn.cursor()

# Check existing columns
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' ORDER BY ordinal_position")
existing = [row[0] for row in cur.fetchall()]
print(f"Existing columns ({len(existing)}): {existing}")

# Columns to add
migrations = [
    ("plan", "ALTER TABLE users ADD COLUMN plan VARCHAR(32) NOT NULL DEFAULT 'beta'"),
    ("subscription_expires_at", "ALTER TABLE users ADD COLUMN subscription_expires_at TIMESTAMP"),
    ("beta_expires_at", "ALTER TABLE users ADD COLUMN beta_expires_at TIMESTAMP"),
    ("role", "ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'user'"),
    ("verification_code_hash", "ALTER TABLE users ADD COLUMN verification_code_hash VARCHAR(256)"),
    ("verification_expires_at", "ALTER TABLE users ADD COLUMN verification_expires_at TIMESTAMP"),
    ("phone_otp_hash", "ALTER TABLE users ADD COLUMN phone_otp_hash VARCHAR(512)"),
    ("phone_otp_expires_at", "ALTER TABLE users ADD COLUMN phone_otp_expires_at TIMESTAMP"),
    ("phone_last_sent_at", "ALTER TABLE users ADD COLUMN phone_last_sent_at TIMESTAMP"),
    ("phone_verified", "ALTER TABLE users ADD COLUMN phone_verified BOOLEAN DEFAULT FALSE"),
    ("rejection_reason", "ALTER TABLE users ADD COLUMN rejection_reason TEXT"),
    ("email_verified", "ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE"),
    ("updated_at", "ALTER TABLE users ADD COLUMN updated_at TIMESTAMP"),
]

added = 0
for col_name, sql in migrations:
    if col_name in existing:
        print(f"  SKIP (exists): {col_name}")
    else:
        try:
            cur.execute(sql)
            print(f"  ADDED: {col_name}")
            added += 1
        except Exception as e:
            print(f"  ERROR {col_name}: {e}")

# Also ensure workspaces2 table exists
cur.execute("SELECT to_regclass('public.workspaces2')")
if cur.fetchone()[0] is None:
    print("\nCreating workspaces2 table...")
    cur.execute("""
        CREATE TABLE workspaces2 (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            business_name VARCHAR(255),
            business_type VARCHAR(100),
            description TEXT,
            website VARCHAR(255),
            industry VARCHAR(255),
            logo_path VARCHAR(500),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("  Created workspaces2 table")
else:
    print("\n  SKIP: workspaces2 table already exists")

# Also ensure audit_logs table exists
cur.execute("SELECT to_regclass('public.audit_logs')")
if cur.fetchone()[0] is None:
    print("Creating audit_logs table...")
    cur.execute("""
        CREATE TABLE audit_logs (
            id SERIAL PRIMARY KEY,
            actor VARCHAR(255),
            action VARCHAR(64),
            user_id INTEGER REFERENCES users(id),
            meta TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("  Created audit_logs table")
else:
    print("  SKIP: audit_logs table already exists")

# Verify final state
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' ORDER BY ordinal_position")
final = [row[0] for row in cur.fetchall()]
print(f"\nFinal columns ({len(final)}): {final}")
print(f"\nMigration complete! Added {added} columns.")

cur.close()
conn.close()
