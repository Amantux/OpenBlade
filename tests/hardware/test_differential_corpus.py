"""Off-rig guard for the read-only differential: its corpus filter must select cases.

The on-rig differential asserts a minimum case count too, but only on the rig;
this catches an emptied or mis-filtered corpus in ordinary CI first.
"""

from __future__ import annotations

from tests.hardware.readonly.conftest import read_corpus
from tests.hardware.readonly.test_readonly_rig import MIN_DIFFERENTIAL_CASES, _is_read_only


def test_differential_corpus_has_minimum_read_only_cases() -> None:
    cases = [c for c in read_corpus() if _is_read_only(c)]
    assert MIN_DIFFERENTIAL_CASES > 0
    assert len(cases) >= MIN_DIFFERENTIAL_CASES
