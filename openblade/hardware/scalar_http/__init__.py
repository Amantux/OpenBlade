"""HTTP client backend for a real Quantum Scalar i3 AML Web Services endpoint.

This package speaks the real i3 dialect (cookie-session auth, XML/JSON content
negotiation, ``moveMedium`` robotics) so OpenBlade can drive a physical library's
control plane over the network. The data path (LTFS read/write) stays host-side
(see ``openblade/hardware/ltfs.py``); the Web Services API carries no file data.
"""

from openblade.hardware.scalar_http.errors import ScalarHttpError
from openblade.hardware.scalar_http.library_backend import ScalarHttpLibraryBackend
from openblade.hardware.scalar_http.session import ScalarHttpSession

__all__ = ["ScalarHttpError", "ScalarHttpLibraryBackend", "ScalarHttpSession"]
