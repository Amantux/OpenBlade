# Quantum Web Services reference specs

Authoritative vendor documentation for the Quantum AML (Scalar i3/i6) and iBlade
Web Services surfaces that OpenBlade emulates. These are the source-of-truth specs
behind the parity tracker (`openblade/emulator_contract/openblade_iblade_rev_a_parity.json`).

## ⚠️ Not committed — local reference only

The PDFs and their extracted `.txt` are **copyrighted Quantum material**
("© Quantum Corporation, all rights reserved") and are **gitignored**. This is a
public repository; do not commit the documents themselves. Run `./fetch.sh` to
populate local copies after cloning.

## Documents

| File (local, gitignored) | Part # / Rev | Covers | Source |
| --- | --- | --- | --- |
| `webservices_i6k_i3_i6_RevD_6-68185-01.pdf` | 6-68185-01 Rev D (Nov 2019) | Scalar i6000/i3/i6 AML Web Services — `/aml/*` surface | [qsupport](https://qsupport.quantum.com/kb/flare/content/Scalar_i6000/downloads/SupDocs/6-68185-01_RevD_WebServices_i6k_i3_i6.pdf) |
| `iblade_webservices_RevA_6-68634-01.pdf` | 6-68634-01 Rev A (Sep 2017) | Scalar i3/i6 Scalar-LTFS & Windows iBlade Web Services — `/iblade/*` surface | [qsupport](https://qsupport.quantum.com/kb/flare/content/Scalar_i3/downloads/SupDocs/6-68634-01_iBlade_WebServicesGuide_RevA.pdf) |

`./fetch.sh` also extracts a `-layout` text rendering next to each PDF (grep-able,
and the input format expected by `tools/emulator_spec/build_manual_matrix.py --input`).

## What these unblock (open parity gaps)

The following gaps were deferred pending these specs (see the parity investigation):

- **f02-auth-content, gap 2** — faithful Rev A XML response *envelope* shape.
  iBlade doc has the response format; grep the iBlade `.txt` for `xml` / `Accept`.
- **f04-hosts, gap 1** — reboot-required signaling on host updates.
  iBlade doc, `reboot` (12 hits). Confirms which host field changes require a reboot.
- **f04-hosts, gap 2** — guide-accurate host field defaulting.
- **f05-jobs, gap 1** — exact Rev A job-state transition adjacency (to tighten the
  permissive matrix in `routes_iblade.py::_validate_job_transition`).

The AML manual (Rev D) is the reference for the `/aml/*` matrix
(`quantum_i3_rev_h_matrix.json`) and the `windows`/`blade` resource families.
