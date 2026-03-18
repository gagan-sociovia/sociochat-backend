"""Test phone normalization — run from backend/Sociovia/whatsapp/"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whatsapp.utils import normalize_phone_robust, extract_all_phones

tests = [
    ("9876543210",                    "919876543210"),
    ("+91 98765 43210",               "919876543210"),
    ("919876543210",                  "919876543210"),
    ("09876543210",                   "919876543210"),
    ("+1 650 555 1234",               "16505551234"),
    ("+277780336483",                 "277780336483"),
    ("8527727496, 9999346509",        "918527727496"),
    ("+277780336483,+919650044539",   "277780336483"),
    ("(+277780336483,+919650044539)", "277780336483"),
    ("9.19E+11",                      "919000000000"),
    (9190000000.0,                    "919190000000"),
    ("+91 (987) 654-3210",            "919876543210"),
    ("9876543210 or 9123456789",      "919876543210"),
    ("9876543210;9123456789",         "919876543210"),
    (None,                            None),
    ("",                              None),
    ("abc",                           None),
]

passed = 0
failed = 0
for raw, expected in tests:
    result = normalize_phone_robust(raw)
    ok = result == expected
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: input={repr(raw)} expected={expected} got={result}")

print(f"\nnormalize_phone_robust: {passed}/{len(tests)} passed, {failed} failed")

# extract_all_phones tests
a1 = extract_all_phones("8527727496, 9999346509")
a2 = extract_all_phones("+277780336483,+919650044539")

ok1 = a1 == ["918527727496", "919999346509"]
ok2 = a2 == ["277780336483", "919650044539"]

print(f"\nextract_all_phones test 1: {'PASS' if ok1 else 'FAIL'} -> {a1}")
print(f"extract_all_phones test 2: {'PASS' if ok2 else 'FAIL'} -> {a2}")
