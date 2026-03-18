# crm_teardown_tables.py
"""
Drop CRM tables created by crm_management.init_models().
This is destructive. BACKUP your DB before running in production.
"""

import sys
from flask import Flask
from test import app  # replace with the module that creates your `app` object
# e.g. from test import app or from example_app import create_app; app = create_app()

# If your entrypoint exposes create_app instead, instantiate app:
# from example_app import create_app
# app = create_app()

# Import the module that defines init_models (so we can call it inside app context)
from SocioviaCrm.models import init_models  # adjust if your package name is different

def drop_crm_tables():
    with app.app_context():
        # Ensure models are defined on current_app.crm_models
        try:
            init_models()
        except Exception as e:
            print("Warning: init_models() raised:", e)

        crm_models = getattr(app, "crm_models", None) or getattr(__import__("crm_management").crm_management, "crm_models", None) or getattr(__import__("crm_management.models"), "crm_models", None)
        # primary attempt to get current_app.crm_models
        crm_models = getattr(app, "crm_models", getattr(__import__("crm_management"), "crm_models", None)) or getattr(app, "crm_models", None)

        # fallback: try current_app
        from flask import current_app
        crm_models = getattr(current_app, "crm_models", None)

        if not crm_models:
            print("No crm_models found on current_app. Attempting to import models directly.")
            # Try to access classes by importing module and calling init_models
            try:
                import crm_management.models as mmod
                mmod.init_models()
                crm_models = getattr(current_app, "crm_models", None)
            except Exception as e:
                print("Failed to initialize crm models:", e)
                sys.exit(1)

        db = current_app.db
        engine = db.engine

        # order matters because of FKs: drop child tables first
        drop_order = ["activities", "tasks", "leads", "contacts", "campaigns", "settings", "workspaces"]
        dropped = []
        for tname in drop_order:
            # find table object in the model dict
            table_obj = None
            for cls in crm_models.values():
                try:
                    if hasattr(cls, "__tablename__") and cls.__tablename__ == tname:
                        table_obj = cls.__table__
                        break
                except Exception:
                    continue
            if not table_obj:
                print(f"Table object for '{tname}' not found in crm_models; attempting raw DROP TABLE IF EXISTS")
                try:
                    engine.execute(f"DROP TABLE IF EXISTS {tname} CASCADE;")
                    print(f"Dropped (raw) table {tname}")
                    dropped.append(tname)
                except Exception as e:
                    print(f"Failed to raw-drop table {tname}: {e}")
                continue

            try:
                print(f"Dropping table {tname} ...")
                table_obj.drop(engine, checkfirst=True)
                print(f"Dropped table {tname}")
                dropped.append(tname)
            except Exception as e:
                print(f"Error dropping table {tname}: {e}")
        print("Dropped tables:", dropped)

if __name__ == "__main__":
    drop_crm_tables()
