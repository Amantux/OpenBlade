"""Unit tests for tools/hardware/allowlist.py — run without the rig."""

from __future__ import annotations

import pytest

from tools.hardware.allowlist import (
    EXIT_OK,
    EXIT_VIOLATION,
    AllowlistError,
    check_subset,
    format_barcodes,
    main,
    parse_barcodes,
)


def test_parse_barcodes_splits_commas_and_whitespace() -> None:
    assert parse_barcodes(" a00001l8, A00002L8\nA00003L8  a00001L8 ") == [
        "A00001L8",
        "A00002L8",
        "A00003L8",
    ]


def test_check_subset_accepts_subset() -> None:
    assert check_subset(["A1"], ["A1", "A2"]) == ["A1"]


def test_check_subset_outside_allowlist_names_offenders() -> None:
    with pytest.raises(AllowlistError, match="PROD9, PROD8"):
        check_subset(["A1", "PROD9", "PROD8"], ["A1", "A2"])


def test_check_subset_empty_allowlist_fails_closed() -> None:
    with pytest.raises(AllowlistError, match="fail closed"):
        check_subset(["A1"], [])


def test_check_subset_empty_request_refused() -> None:
    with pytest.raises(AllowlistError, match="No barcodes"):
        check_subset([], ["A1"])


def test_main_offender_exits_2_with_message(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SACRIFICIAL_BARCODES", "A1,A2")
    assert main(["--barcodes", "A1 PROD9"]) == EXIT_VIOLATION
    assert "PROD9" in capsys.readouterr().err


def test_main_falls_back_to_env_barcodes(monkeypatch) -> None:
    monkeypatch.setenv("SACRIFICIAL_BARCODES", "A1 A2")
    monkeypatch.setenv("BARCODES", "a2")
    assert main([]) == EXIT_OK


def test_main_unset_allowlist_exits_2(monkeypatch) -> None:
    monkeypatch.delenv("SACRIFICIAL_BARCODES", raising=False)
    assert main(["--barcodes", "A1"]) == EXIT_VIOLATION


def test_main_success_prints_only_normalized_list_on_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SACRIFICIAL_BARCODES", "A1 A2 A3")
    assert main(["--barcodes", " a2, A1\na2 "]) == EXIT_OK
    out, err = capsys.readouterr()
    assert out == "A2,A1\n"
    assert "OK" in err


def test_main_refusal_prints_nothing_on_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SACRIFICIAL_BARCODES", "A1")
    assert main(["--barcodes", "PROD9"]) == EXIT_VIOLATION
    assert capsys.readouterr().out == ""


def test_allowlist_output_round_trips_through_conftest_parser(monkeypatch, capsys) -> None:
    from tests.hardware.conftest import split_barcode_env

    monkeypatch.setenv("SACRIFICIAL_BARCODES", "TST001L8,TST002L8,TST003L8")
    assert main(["--barcodes", "tst003l8 TST001L8,tst003L8"]) == EXIT_OK
    printed = capsys.readouterr().out.strip()
    assert split_barcode_env(printed) == ["TST003L8", "TST001L8"]
    assert format_barcodes(split_barcode_env(printed)) == printed


def test_conftest_parser_still_accepts_spaced_comma_lists() -> None:
    from tests.hardware.conftest import split_barcode_env

    assert split_barcode_env(" A1 , A2,,") == ["A1", "A2"]
    assert split_barcode_env("") == []
