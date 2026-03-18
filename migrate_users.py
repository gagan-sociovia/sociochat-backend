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
    ("auto_login_token_hash", "ALTER TABLE users ADD COLUMN auto_login_token_hash VARCHAR(256)"),
    ("auto_login_token_fingerprint", "ALTER TABLE users ADD COLUMN auto_login_token_fingerprint VARCHAR(64)"),
    ("auto_login_token_expires_at", "ALTER TABLE users ADD COLUMN auto_login_token_expires_at TIMESTAMP"),
    ("email_verified_at", "ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP"),
    ("phone_verified_at", "ALTER TABLE users ADD COLUMN phone_verified_at TIMESTAMP"),
    ("rejection_reason", "ALTER TABLE users ADD COLUMN rejection_reason TEXT"),
    ("email_verified", "ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE"),
    ("pending_plan", "ALTER TABLE users ADD COLUMN pending_plan VARCHAR(32)"),
    ("pending_billing", "ALTER TABLE users ADD COLUMN pending_billing VARCHAR(32)"),
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

# Ensure expected auth indexes exist
index_migrations = [
    (
        "ix_users_auto_login_token_fingerprint",
        "CREATE INDEX IF NOT EXISTS ix_users_auto_login_token_fingerprint ON users (auto_login_token_fingerprint)"
    ),
]

for index_name, sql in index_migrations:
    try:
        cur.execute(sql)
        print(f"  INDEX OK: {index_name}")
    except Exception as e:
        print(f"  INDEX ERROR {index_name}: {e}")

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

# Ensure workspaces2 has all columns expected by the current Workspace model
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'workspaces2' ORDER BY ordinal_position")
existing_workspaces = [row[0] for row in cur.fetchall()]
print(f"Existing workspaces2 columns ({len(existing_workspaces)}): {existing_workspaces}")

workspace_migrations = [
    ("registered_address", "ALTER TABLE workspaces2 ADD COLUMN registered_address VARCHAR(500)"),
    ("address_line", "ALTER TABLE workspaces2 ADD COLUMN address_line VARCHAR(500)"),
    ("city", "ALTER TABLE workspaces2 ADD COLUMN city VARCHAR(100)"),
    ("district", "ALTER TABLE workspaces2 ADD COLUMN district VARCHAR(100)"),
    ("pin_code", "ALTER TABLE workspaces2 ADD COLUMN pin_code VARCHAR(20)"),
    ("country", "ALTER TABLE workspaces2 ADD COLUMN country VARCHAR(100) DEFAULT 'India'"),
    ("b2b_b2c", "ALTER TABLE workspaces2 ADD COLUMN b2b_b2c VARCHAR(20)"),
    ("audience_description", "ALTER TABLE workspaces2 ADD COLUMN audience_description TEXT"),
    ("competitor_direct_1", "ALTER TABLE workspaces2 ADD COLUMN competitor_direct_1 VARCHAR(255)"),
    ("competitor_direct_2", "ALTER TABLE workspaces2 ADD COLUMN competitor_direct_2 VARCHAR(255)"),
    ("competitor_indirect_1", "ALTER TABLE workspaces2 ADD COLUMN competitor_indirect_1 VARCHAR(255)"),
    ("competitor_indirect_2", "ALTER TABLE workspaces2 ADD COLUMN competitor_indirect_2 VARCHAR(255)"),
    ("social_links", "ALTER TABLE workspaces2 ADD COLUMN social_links TEXT"),
    ("usp", "ALTER TABLE workspaces2 ADD COLUMN usp TEXT"),
    ("creatives_path", "ALTER TABLE workspaces2 ADD COLUMN creatives_path VARCHAR(500)"),
    ("remarks", "ALTER TABLE workspaces2 ADD COLUMN remarks TEXT"),
]

workspace_added = 0
for col_name, sql in workspace_migrations:
    if col_name in existing_workspaces:
        print(f"  WORKSPACE SKIP (exists): {col_name}")
    else:
        try:
            cur.execute(sql)
            print(f"  WORKSPACE ADDED: {col_name}")
            workspace_added += 1
        except Exception as e:
            print(f"  WORKSPACE ERROR {col_name}: {e}")

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
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'workspaces2' ORDER BY ordinal_position")
final_workspaces = [row[0] for row in cur.fetchall()]
print(f"Final workspaces2 columns ({len(final_workspaces)}): {final_workspaces}")
print(f"\nMigration complete! Added {added} user columns and {workspace_added} workspace columns.")

cur.close()
conn.close()
