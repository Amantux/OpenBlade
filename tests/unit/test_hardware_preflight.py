"""Unit tests for tools.hardware.preflight — every check against fakes, no hardware."""

from __future__ import annotations

from collections.abc import Mapping

from tests.fakes.mtx import StatefulMtxRunner
from tools.hardware import preflight as pf

CHANGER = "/dev/sg9"
DRIVES = ["/dev/nst0", "/dev/nst1", "/dev/nst2"]
SERIALS = {"/dev/nst0": "SN0", "/dev/nst1": "SN1", "/dev/nst2": "SN2"}


class FakeAppliance:
    def __init__(self, code: int = 200) -> None:
        self.code = code
        self.calls: list[str] = []

    def login(self) -> int:
        self.calls.append("login")
        return self.code

    def logout(self) -> int:
        self.calls.append("logout")
        return 200


def _env(**over: str) -> dict[str, str]:
    env = {
        "OPENBLADE_CHANGER_DEVICE": CHANGER,
        "OPENBLADE_DRIVE_DEVICES": ",".join(DRIVES),
        "OPENBLADE_APPLIANCE_URL": "https://i3.example",
        "OPENBLADE_APPLIANCE_USER": "ops",
        "OPENBLADE_APPLIANCE_PASSWORD": "s3cret-pw",
        "SACRIFICIAL_BARCODES": "AAA001L8,AAA002L8",
    }
    env.update(over)
    return {k: v for k, v in env.items() if v != "<unset>"}


def _host(
    env: Mapping[str, str] | None = None,
    *,
    serials: dict[str, str] | None = None,
    appliance: FakeAppliance | None = None,
) -> tuple[pf.Host, StatefulMtxRunner]:
    runner = StatefulMtxRunner(
        CHANGER,
        drives=3,
        slots=10,
        barcodes={1: "AAA001L8", 2: "AAA002L8", 3: "OTHER3L8"},
        drive_serials=serials if serials is not None else SERIALS,
    )
    fake = appliance or FakeAppliance()
    host = pf.Host(
        env=env if env is not None else _env(),
        runner=runner,
        which=lambda t: f"/usr/bin/{t}",
        exists=lambda p: True,
        readable=lambda p: True,
        by_id=lambda: [f"{d}|IBM-ULTRIUM-TD8_{s}-nst" for d, s in SERIALS.items()],
        appliance=lambda _env: fake,
    )
    return host, runner


def _by(checks: list[pf.Check], status: str) -> list[pf.Check]:
    return [c for c in checks if c.status == status]


def test_happy_path_has_no_fail_or_warn_and_never_moves_media() -> None:
    host, runner = _host()
    checks = pf.run_preflight(host, "destructive")
    assert _by(checks, "FAIL") == [] and _by(checks, "WARN") == []
    verbs = {c[3] for c in runner.calls if c[0] == "mtx"}
    assert verbs == {"status"}  # read-only: inquiry/listing only


def test_missing_env_fail_names_the_var() -> None:
    host, _ = _host(_env(OPENBLADE_APPLIANCE_PASSWORD="<unset>"))
    fails = _by(pf.check_env(host, "readonly"), "FAIL")
    assert [c.name for c in fails] == ["env:OPENBLADE_APPLIANCE_PASSWORD"]
    assert "OPENBLADE_APPLIANCE_PASSWORD" in fails[0].detail


def test_password_value_is_never_emitted() -> None:
    host, _ = _host()
    assert all("s3cret-pw" not in (c.detail + c.fix) for c in pf.run_preflight(host, "readonly"))


def test_allowlist_required_for_destructive_only() -> None:
    host, _ = _host(_env(SACRIFICIAL_BARCODES="<unset>"))
    assert _by(pf.run_preflight(host, "readonly"), "FAIL") == []
    assert {c.name for c in _by(pf.run_preflight(host, "destructive"), "FAIL")} == {
        "env:SACRIFICIAL_BARCODES",
        "allowlist",
    }


def test_duplicate_serials_fail() -> None:
    dup = {"/dev/nst0": "SN0", "/dev/nst1": "SN0", "/dev/nst2": "SN2"}
    host, _ = _host(serials=dup)
    fails = _by(pf.check_devices(host), "FAIL")
    assert any(c.name == "serials" and "SN0" in c.detail for c in fails)


def test_missing_serial_and_unreadable_device_fail() -> None:
    host, _ = _host(serials={"/dev/nst0": "SN0", "/dev/nst1": "SN1"})
    host.readable = lambda p: p != CHANGER
    names = {c.name for c in _by(pf.check_devices(host), "FAIL")}
    assert names == {"inquiry:/dev/nst2", f"device:{CHANGER}"}


def test_missing_by_id_link_fails() -> None:
    host, _ = _host()
    host.by_id = lambda: []
    assert {c.name for c in _by(pf.check_devices(host), "FAIL")} == {
        "by-id:SN0",
        "by-id:SN1",
        "by-id:SN2",
    }


def test_serial_map_must_agree_with_inquiry_order() -> None:
    ok, _ = _host(_env(OPENBLADE_DRIVE_SERIAL_MAP="SN0:0,SN1:1,SN2:2"))
    assert _by(pf.check_devices(ok), "FAIL") == []
    bad, _ = _host(_env(OPENBLADE_DRIVE_SERIAL_MAP="SN1:0,SN0:1,SN2:2"))
    assert [c.name for c in _by(pf.check_devices(bad), "FAIL")] == ["serial-map"]
    junk, _ = _host(_env(OPENBLADE_DRIVE_SERIAL_MAP="nonsense"))
    assert [c.name for c in _by(pf.check_devices(junk), "FAIL")] == ["serial-map"]


def test_changer_drive_count_mismatch_fails() -> None:
    host, _ = _host(_env(OPENBLADE_DRIVE_DEVICES="/dev/nst0,/dev/nst1"))
    checks, status = pf.check_changer(host)
    assert status is not None and checks[0].status == "FAIL" and "3 drives" in checks[0].detail


def test_allowlisted_barcode_loaded_fails() -> None:
    host, runner = _host()
    runner.drives[0] = ("AAA001L8", 1)
    runner.slots[1] = None
    _, status = pf.check_changer(host)
    fails = _by(pf.check_allowlist(host, status, "destructive"), "FAIL")
    assert any("loaded" in c.detail and "AAA001L8" in c.detail for c in fails)


def test_allowlisted_barcode_absent_from_inventory_fails() -> None:
    host, _ = _host(_env(SACRIFICIAL_BARCODES="AAA001L8,ZZZ999L8"))
    _, status = pf.check_changer(host)
    fails = _by(pf.check_allowlist(host, status, "destructive"), "FAIL")
    assert any("ZZZ999L8" in c.detail for c in fails)


def test_armed_env_fails() -> None:
    host, _ = _host(_env(OPENBLADE_REAL_HARDWARE_ENABLED="true"))
    assert pf.check_armed(host).status == "FAIL"


def test_armed_env_fails_an_otherwise_healthy_run() -> None:
    # main() exits EXIT_FAIL iff any check FAILs; armed must be enough on its own.
    host, _ = _host(_env(OPENBLADE_REAL_HARDWARE_ENABLED="true"))

    checks = pf.run_preflight(host, "readonly")

    assert [c.name for c in _by(checks, "FAIL")] == ["armed"]


def test_missing_tool_fails() -> None:
    host, _ = _host()
    host.which = lambda t: None if t == "mkltfs" else f"/usr/bin/{t}"
    assert [c.name for c in _by(pf.check_tools(host), "FAIL")] == ["tool:mkltfs"]


def test_appliance_login_logout_and_insecure_warn() -> None:
    fake = FakeAppliance()
    host, _ = _host(_env(OPENBLADE_APPLIANCE_INSECURE="true"), appliance=fake)
    checks = pf.check_appliance(host)
    assert fake.calls == ["login", "logout"]
    assert [c.status for c in checks] == ["WARN", "PASS"]
    denied, _ = _host(appliance=FakeAppliance(401))
    assert pf.check_appliance(denied)[0].status == "FAIL"


def test_appliance_bad_scheme_is_a_curated_fail() -> None:
    host, _ = _host(_env(OPENBLADE_APPLIANCE_URL="ftp://i3"))
    host.appliance = pf._default_appliance
    (check,) = pf.check_appliance(host)
    assert check.status == "FAIL" and "http" in check.detail
