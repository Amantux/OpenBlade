#!/usr/bin/env python3
"""Simple security agent: verify auth and library listing."""
import requests

BASE='http://localhost:8000'
print('Logging in')
r = requests.post(f"{BASE}/aml/auth/login", json={"name":"admin","password":"password"}, timeout=5)
print('login', r.status_code, r.text)
if r.status_code==200:
    token = r.json().get('token')
    headers={'Authorization': f'Bearer {token}'}
    lib = requests.get(f"{BASE}/api/libraries", headers=headers, timeout=5)
    print('GET /api/libraries', lib.status_code)
    print(lib.text)
else:
    print('Auth failed')
