#!/usr/bin/env bash
# Fetch the Quantum Web Services reference PDFs locally and extract text.
# These are copyrighted vendor docs — they are gitignored and never committed to
# this public repo. Run this once per checkout to populate local reference copies.
set -euo pipefail
cd "$(dirname "$0")"

MANUAL_URL="https://qsupport.quantum.com/kb/flare/content/Scalar_i6000/downloads/SupDocs/6-68185-01_RevD_WebServices_i6k_i3_i6.pdf"
IBLADE_URL="https://qsupport.quantum.com/kb/flare/content/Scalar_i3/downloads/SupDocs/6-68634-01_iBlade_WebServicesGuide_RevA.pdf"

curl -sSL -A "Mozilla/5.0" -o webservices_i6k_i3_i6_RevD_6-68185-01.pdf "$MANUAL_URL"
curl -sSL -A "Mozilla/5.0" -o iblade_webservices_RevA_6-68634-01.pdf   "$IBLADE_URL"

if command -v pdftotext >/dev/null 2>&1; then
  pdftotext -layout webservices_i6k_i3_i6_RevD_6-68185-01.pdf webservices_i6k_i3_i6_RevD_6-68185-01.txt
  pdftotext -layout iblade_webservices_RevA_6-68634-01.pdf   iblade_webservices_RevA_6-68634-01.txt
  echo "Extracted text alongside the PDFs (grep-able; feeds tools/emulator_spec/build_manual_matrix.py)."
else
  echo "pdftotext not found — install poppler-utils to generate .txt for matrix building." >&2
fi
echo "Done. Files are local-only (gitignored)."
