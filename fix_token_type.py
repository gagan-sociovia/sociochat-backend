import os
from app import app
from models import db
from whatsapp.models import WhatsAppAccount

with app.app_context():
    accounts = WhatsAppAccount.query.all()
    count = 0
    for acc in accounts:
        is_temp = False
        display_phone = acc.display_phone_number or ""
        
        if display_phone.startswith("+1 555") or "15558" in display_phone.replace(" ", "").replace("-", ""):
            is_temp = True
            
        token = acc.get_access_token()
        if token and len(token) < 200:
            is_temp = True
            
        if is_temp and acc.token_type != "temporary":
            acc.token_type = "temporary"
            count += 1
            print(f"Fixed account {acc.id} ({display_phone}) -> temporary")
            
    db.session.commit()
    print(f"Fixed {count} accounts.")
