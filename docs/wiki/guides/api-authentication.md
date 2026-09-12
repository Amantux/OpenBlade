# API authentication (native REST surface)

By default OpenBlade's native REST API has **no authentication at all**. Anyone
who can open a TCP connection to the port can list your catalog, enqueue an
archive, format a tape, or run the test runner. That default is deliberate — it
is what every existing deployment, script and CI job relies on — but it is only
safe on a loopback-only or otherwise isolated port.

This page covers the bearer-token layer that closes that hole when you turn it
on, what it does *not* cover, and how to roll it out without breaking anything.

> **Scope warning up front.** This token guards the **OpenBlade-native** surface
> only. The Quantum AML emulator surface (`/aml/*`, `/iblade/*`) has its own,
> separate session login and is completely unaffected — see
> [What this does not protect](#what-this-does-not-protect).

---

## Turning it on

Set one environment variable and restart:

```bash
# .env / docker-compose environment / systemd EnvironmentFile
OPENBLADE_API_TOKEN=9f2c1d5b8a4e7f3016d2b9c4e8a1f70b5d3c2e9a
```

Generate the value with something you did not think of yourself:

```bash
python3 -c "import secrets; print(secrets.token_hex(24))"
```

Then every native request needs the token:

```bash
curl -H "Authorization: Bearer $OPENBLADE_API_TOKEN" http://localhost:8000/jobs/
```

Without it you get a flat 401 and nothing else happens — the request is rejected
before it reaches any route handler, so a missing token can never half-perform an
operation.

```json
{
  "error": "Unauthorized",
  "detail": "This endpoint requires a bearer token. Send 'Authorization: Bearer <token>' using the token configured via OPENBLADE_API_TOKEN or OPENBLADE_API_TOKEN_FILE."
}
```

The response never echoes the token you sent, and the server log line records
the method, path and source address only.

### Leaving it off

If neither variable is set, authentication is disabled and behaviour is
byte-for-byte what it was before — but the server says so, loudly, once per
start:

```
WARNING  openblade.api.api_auth  !!! NATIVE API AUTHENTICATION IS DISABLED !!!
Every OpenBlade-native endpoint (including /jobs, /ltfs/format and
/api/test-runner) is reachable by anyone who can reach this port. Set
OPENBLADE_API_TOKEN or OPENBLADE_API_TOKEN_FILE to require
'Authorization: Bearer <token>'.
```

If you see that line in a deployment reachable from anything but localhost, you
have a problem to fix, not a message to filter out.

---

## Reading the token from a file

Putting a secret in an environment variable means it shows up in `docker
inspect`, in `/proc/<pid>/environ`, and in anything that dumps the environment
on crash. For a real deployment, use a file instead:

```bash
install -m 600 /dev/null /etc/openblade/api-token
python3 -c "import secrets; print(secrets.token_hex(24))" > /etc/openblade/api-token
chmod 600 /etc/openblade/api-token
```

```bash
OPENBLADE_API_TOKEN_FILE=/etc/openblade/api-token
```

Rules, in order of the ones that will bite you:

| Situation | What happens |
|---|---|
| Both `OPENBLADE_API_TOKEN_FILE` and `OPENBLADE_API_TOKEN` set | **The file wins.** The env var is ignored and a warning says so. |
| File mode looser than `0600` | Auth still works; a startup warning names the file and its mode. Fix it — any local user can read the token. |
| File missing, unreadable, or empty | **Startup fails.** Failing open here would silently give you no auth at all on a server you believed was protected. |
| Either value is an empty string | Treated as unset. The HA-style "cleared field writes `\"\"`" case must not beat a real default. |

Surrounding whitespace and the trailing newline are stripped, so
`echo "$TOKEN" > file` does the right thing.

### docker-compose

```yaml
services:
  openblade:
    environment:
      OPENBLADE_API_TOKEN_FILE: /run/secrets/openblade_api_token
    secrets:
      - openblade_api_token

secrets:
  openblade_api_token:
    file: ./secrets/openblade-api-token
```

---

## What is protected

Everything on the native surface, with three exemptions. There is no per-route
opt-in list to maintain: enforcement is a single middleware in
`openblade/api/api_auth.py` that classifies by path, so a route added tomorrow is
protected the day it exists. `tests/integration/test_api_auth_sweep.py` walks
`app.openapi()` and fails if any native operation answers anything but 401
without a credential.

| Path | Auth enabled | Auth disabled |
|---|---|---|
| `/inventory`, `/jobs`, `/archive`, `/restore`, `/ltfs`, `/catalog`, `/cartridges`, `/volume-groups`, `/dashboard`, `/storage`, `/nas`, `/virtual`, `/safety`, `/tape-ops`, `/api/*` | token required | open |
| `/docs`, `/redoc`, `/openapi.json` | token required | open |
| `/health`, `/healthz`, `/readyz` | **open** | open |
| `/aml/*`, `/iblade/*` | AML session (unchanged) | AML session (unchanged) |

`/health`, `/healthz` and `/readyz` stay open on purpose so container health
checks, load balancers and uptime monitors keep working without a credential.
They report liveness, readiness and the backend name — no inventory, no
configuration, no catalog data. `/version` and `/error-codes` are *not* exempt.

CORS preflight (`OPTIONS`) is not gated; preflight requests carry no credentials
by definition.

---

## What this does not protect

**The AML emulator surface.** `/aml/*` and `/iblade/*` are a wire-compatible
reimplementation of the Quantum Scalar i3 API, and their authentication is part
of that contract: a `POST /aml/users/login` session, carried as a `sessionID`
cookie or as a bearer token. The native API token neither unlocks nor blocks
them. If you need that surface locked down, that is a separate change to the AML
session layer — do not assume this token covers it.

**Authorisation.** There is one token and it is all-or-nothing. There are no
scopes, no read-only tokens, no per-user attribution. Anyone holding the token
can format a tape.

**Transport.** The token travels in a plain header. Terminate TLS in front of
OpenBlade, or keep the port on a trusted network. A bearer token on plaintext
HTTP is a token you have published.

**Rotation.** Changing the token means editing the env var or file and
restarting. There is no online rotation and no grace period for the old value.

**The safety gates.** Authentication is orthogonal to
[the safety model](safety-model.md). An authenticated caller still has to do the
dry-run → token → confirm dance to format a tape.

---

## Rolling it out without an outage

Every client of the native API needs the header before you flip the switch.
In a default deployment that is at least:

1. **The React frontend**, if it talks to the API from a browser. It has no
   place to put a bearer token that a browser user cannot also read — if you
   serve the UI publicly, front it with a reverse proxy that injects the header
   and authenticates users itself, rather than shipping the token to the client.
2. **Your own scripts and cron jobs.**
3. **Monitoring**, unless it only polls `/health`.

A safe sequence:

```bash
# 1. Pick the token, but do not enable it yet.
python3 -c "import secrets; print(secrets.token_hex(24))" | tee /etc/openblade/api-token
chmod 600 /etc/openblade/api-token

# 2. Teach every client to send it. The header is harmless while auth is off.
export OPENBLADE_TOKEN=$(cat /etc/openblade/api-token)
curl -H "Authorization: Bearer $OPENBLADE_TOKEN" http://localhost:8000/jobs/   # still 200

# 3. Enable, restart, and verify both halves.
OPENBLADE_API_TOKEN_FILE=/etc/openblade/api-token  # in the service environment
curl -o /dev/null -w '%{http_code}\n' http://localhost:8000/jobs/                       # expect 401
curl -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $OPENBLADE_TOKEN" \
     http://localhost:8000/jobs/                                                        # expect 200
curl -o /dev/null -w '%{http_code}\n' http://localhost:8000/health                      # expect 200
```

Step 2 is the one people skip. Sending the header while auth is still off is a
no-op, which is exactly why it is the right order: you find the client you forgot
*before* it starts failing.

---

## A wrinkle worth knowing: two credentials, one header

A few native routes — `/api/libraries`, `/status/library`, `/status/catalog`,
`/system/config-summary` — additionally require an **AML session**, because they
read library state through the emulator layer. With native auth enabled, the
`Authorization` header is taken by the API token, so those routes need the AML
session delivered the other way: as the `sessionID` cookie that
`POST /aml/users/login` sets.

```bash
# Log in for the AML session cookie, then send both credentials.
curl -c /tmp/ob-cookies -X POST http://localhost:8000/aml/users/login \
     -H 'Content-Type: application/json' \
     -d '{"name":"admin","password":"password"}'

curl -b /tmp/ob-cookies -H "Authorization: Bearer $OPENBLADE_TOKEN" \
     http://localhost:8000/api/libraries
```

`require_auth` prefers the cookie and only falls back to the bearer token, so
this works in both modes and is what the i3 compliance suite does.

---

## Troubleshooting

**Everything returns 401, including with the token.** Check the server actually
loaded the token you think it did — a `OPENBLADE_API_TOKEN_FILE` with a stray
trailing space in the *path*, or a file the service user cannot read, fails
startup rather than falling back. Look for the startup line.

**`/api/libraries` returns 401 with a valid token.** That is the AML session,
not the API token. See [the wrinkle above](#a-wrinkle-worth-knowing-two-credentials-one-header).

**`/aml/...` returns 401 and you are sending the API token.** Expected: the AML
surface does not accept it. Log in with `POST /aml/users/login`.

**A 401 with `"code": "AML_AUTH_REQUIRED"`** came from the AML session layer.
A 401 with `"error": "Unauthorized"` came from this one. The body tells you which
gate you hit.

**The frontend stopped working.** It has no token. See
[rolling it out](#rolling-it-out-without-an-outage).

---

## Related

- [Safety model](safety-model.md) — the destructive-operation gates, which are
  independent of authentication
- [Formatting tapes](formatting-tapes.md) — the dry-run → token → confirm flow
- [Jobs & monitoring](jobs-and-monitoring.md) — the health endpoints that stay open
