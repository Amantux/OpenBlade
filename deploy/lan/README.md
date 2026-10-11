# Exposing OpenBlade on a LAN

The default `docker-compose.yml` binds every port to `127.0.0.1`. This override
publishes the stack on the LAN in **production mode** (fail-closed auth).

| Service    | LAN port | Container |
|------------|----------|-----------|
| api        | 8090     | 8000      |
| web (SPA)  | 8091     | 80        |
| web-flask  | 8092     | 8080      |

## Steps

1. `cp deploy/lan/.env.example deploy/lan/.env` (gitignored) and fill every value.
   Generate each secret with
   `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
2. `make lan-up` (build + start, project `openblade-lan`); `make lan-down` to stop.

Compose aborts naming the variable if any required secret is unset, and the api
refuses to start in production with an unset API token, a default AML
admin/service password or service token, or `*` in `OPENBLADE_CORS_ORIGINS`.

## Notes

- The SPA's nginx proxies `/api` and `/aml` to `api:8000`, so the browser only
  talks to port 8091 (same origin) and CORS does not apply to it. Set
  `OPENBLADE_CORS_ORIGINS` to explicit origins for any other browser client.
- The test-runner route is disabled (404) in production.
- **HTTP on a LAN is cleartext**: tokens and passwords cross the wire in the
  clear. Terminate TLS in a reverse proxy next, then set
  `OPENBLADE_WEB_SECURE_COOKIES=true`.
