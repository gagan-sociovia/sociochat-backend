import urllib.request
import json
import traceback

try:
    url = "http://127.0.0.1:5000/api/whatsapp/templates?account_id=undefined&status=APPROVED"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = response.read().decode('utf-8')
        print(f"Status: {response.status}")
        print("Response JSON:")
        parsed = json.loads(data)
        print(json.dumps(parsed, indent=2)[:500] + "...")
except Exception as e:
    traceback.print_exc()
