#!/usr/bin/env python3
"""Security agent: check security headers on API endpoints."""
import requests

URL='http://localhost:8000/health'
print('Checking', URL)
r = requests.get(URL, timeout=5)
print('status', r.status_code)
for h in ['x-content-type-options','x-frame-options','strict-transport-security','content-security-policy','referrer-policy']:
    print(h, r.headers.get(h) or r.headers.get(h.title()) or '(missing)')
