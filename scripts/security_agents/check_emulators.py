#!/usr/bin/env python3
"""Inventory agent: list libraries and query emulator health/inventory endpoints."""
import requests

BASE='http://localhost:8000'
print('Logging in')
r = requests.post(f"{BASE}/aml/auth/login", json={"name":"admin","password":"password"}, timeout=5)
if r.status_code!=200:
    print('login failed', r.status_code); raise SystemExit(1)
token=r.json().get('token')
headers={'Authorization':f'Bearer {token}'}
libs = requests.get(f"{BASE}/api/libraries", headers=headers, timeout=5).json()
print('libraries:', [l['name'] for l in libs])
for l in libs:
    url = l.get('emulator_url')
    if not url:
        continue
    try:
        h = requests.get(url+'/health', timeout=5)
        print(url+'/health', h.status_code)
    except Exception as e:
        print('emulator', url, 'unreachable', e)
    try:
        inv = requests.get(url+'/api/aml/inventory', timeout=5)
        print(url+'/api/aml/inventory', inv.status_code)
    except Exception as e:
        print('inventory failed for', url, e)
