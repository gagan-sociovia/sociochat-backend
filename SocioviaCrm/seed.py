# crm_management/seed.py
from decimal import Decimal
from flask import current_app

def seed_demo_data():
    with current_app.app_context():
        db = current_app.db
        models = current_app.crm_models
        Lead = models["Lead"]
        Campaign = models["Campaign"]
        Setting = models["Setting"]

        db.create_all()
        if db.session.query(Lead).count() == 0:
            l1 = Lead(name="Alice Johnson", email="alice@example.com", status="new", source="Facebook Ad", score=85, value=Decimal("1200.00"))
            l2 = Lead(name="Bob Kumar", email="bob@example.com", status="contacted", source="Google", score=64, value=Decimal("500.00"))
            db.session.add_all([l1, l2])
            db.session.commit()
        if db.session.query(Campaign).count() == 0:
            c1 = Campaign(name="Summer Sale", status="active", objective="Sales", spend=12500, impressions=450000, clicks=12000, leads=450, revenue=43750)
            c2 = Campaign(name="App Install", status="paused", objective="Installs", spend=4200, impressions=150000, clicks=5400, leads=120, revenue=0)
            db.session.add_all([c1, c2])
            db.session.commit()
        if db.session.query(Setting).count() == 0:
            s1 = Setting(name="meta_access_token", value="meta_big_secret_value_example", masked=True)
            s2 = Setting(name="webhook_url", value="https://example.com/webhook", masked=True)
            db.session.add_all([s1, s2])
            db.session.commit()
