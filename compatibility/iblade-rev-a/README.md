# iblade-rev-a — Scalar-LTFS / Windows iBlade (Rev A, Sep 2017)

Scope: the iBlade management surface (`/iblade/*`) as described by the local
iBlade reference manual, Rev A (2017). This profile is a **scaffold**.

- Every case here is `inferred`: none has been checked against an appliance or
  quoted from the manual. They lock current emulator behaviour only.
- The login, logout and 401 cases currently exercise the shared AML auth routes
  (`/aml/users/login`, `/aml/auth/logout`) because no iBlade-specific auth path
  is modelled yet. Replace them with `/iblade/*` paths once confirmed.
- Add `captured` cases next to these files, following `../README.md`.
