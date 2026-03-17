import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

try:
    from app import app, db
    from whatsapp.models import WhatsAppTemplate

    with app.app_context():
        templates = WhatsAppTemplate.query.all()
        
        with open('templates_output_utf8.txt', 'w', encoding='utf-8') as f:
            f.write(f"Total templates found: {len(templates)}\n")
            for t in templates:
                f.write(f"ID: {t.id}, Name: {t.name}, Status: {t.status}, Account ID: {t.account_id}\n")
            
except Exception as e:
    import traceback
    with open('templates_output_utf8.txt', 'w', encoding='utf-8') as f:
        traceback.print_exc(file=f)
