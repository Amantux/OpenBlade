"""Tier-2 media actions: ask, confirm strongly, then do.

These cover the behaviour. The *structural* guarantees — that a media name can
never enter the tier-1 registry, that the facade leaks no reference to the library
backend, that a tier-2 tool cannot run without its authorization — live in
``tests/safety/test_assistant_read_only.py`` with their mutation checks.

Everything here runs against the simulator backend the ``app_context`` fixture
builds, so a "format" really does go through ``FormatService.dry_run`` ->
``SafetyToken`` -> ``FormatService.confirm`` -> the orchestrator, and an "archive"
really does write files and create catalog rows.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from openblade.assistant.errors import (
    MediaNotAuthorizedError,
    MediaRefusedError,
    MediaRegistryViolationError,
)
from openblade.assistant.media_facade import OVERWRITE_WORD
from openblade.assistant.media_tools import (
    MEDIA_TOOL_NAMES,
    ConfirmationGrade,
    MediaAuthorization,
    MediaTool,
    build_media_registry,
    verify_response,
)
from openblade.assistant.setup_tools import SETUP_TOOL_NAMES
from openblade.assistant.tools import READ_ONLY_TOOL_NAMES
from openblade.domain.models import MountMode
from tests.assistant_support import media_facade_for

# The MODULE, not the ``media_facade`` factory function of the same name that
# ``openblade.assistant.__init__`` re-exports — ``import a.b as c`` would bind the
# function, because the package attribute shadows the submodule. Same shape as the
# existing ``setup_facade`` module/function pair.
media_facade_module = importlib.import_module("openblade.assistant.media_facade")

FIRST_BARCODE = "VOL001L9"
SECOND_BARCODE = "VOL002L9"


@pytest.fixture()
def facade(app_context: Any) -> Any:
    return media_facade_for(app_context)


@pytest.fixture()
def registry() -> Any:
    return build_media_registry()


def plan(registry: Any, facade: Any, name: str, **arguments: Any) -> Any:
    return registry.plan(name, facade, arguments)


def run(registry: Any, facade: Any, action: Any, response: str) -> dict[str, Any]:
    authorization = registry.authorize(action, response)
    assert authorization is not None, "the response should have authorized this action"
    return registry.perform(action, facade, authorization)


def free_slot(app_context: Any) -> int:
    """The highest empty storage slot. Hard-coding one breaks when the seed changes."""
    empty = [
        int(slot.slot_id) for slot in app_context.library.inventory().slots if not slot.barcode
    ]
    assert empty, "the fixture library should have an empty slot"
    return max(empty)


def formatted_tape(app_context: Any, barcode: str) -> None:
    """Mark a simulator tape formatted.

    Scaffolding, not the thing under test: the mock LTFS backend refuses to mount
    unformatted media, and an archive test that had to format first would be
    testing two tools at once.
    """
    app_context.ltfs.ensure_tape(barcode).formatted = True


def _rogue(name: str) -> MediaTool:
    return MediaTool(
        name=name,
        description="Should never be registrable.",
        parameters={"type": "object", "properties": {}},
        normalize=lambda arguments: {},
        plan=lambda facade, arguments: {},
        describe=lambda arguments, plan: "nope",
        grade=lambda arguments, plan: (ConfirmationGrade.YES_NO, None),
        apply=lambda facade, action: {},
    )


# ---------------------------------------------------------------------------
# 1. The registry
# ---------------------------------------------------------------------------


def test_media_allowlist_is_the_reviewed_set() -> None:
    """Six operations, spelled out, so widening tier 2 shows up in a diff."""
    assert set(MEDIA_TOOL_NAMES) == {
        "load_tape",
        "unload_drive",
        "move_tape",
        "format_tape",
        "archive_path",
        "restore_path",
    }
    assert build_media_registry().names == MEDIA_TOOL_NAMES


def test_media_registry_rejects_an_unlisted_tool() -> None:
    """MUTATION CHECK: drop the allowlist check in MediaToolRegistry -> this fails."""
    with pytest.raises(MediaRegistryViolationError) as excinfo:
        build_media_registry([_rogue("eject_magazine")])
    assert "media allowlist" in str(excinfo.value)


def test_media_registry_rejects_a_duplicate_name() -> None:
    with pytest.raises(MediaRegistryViolationError) as excinfo:
        build_media_registry([_rogue("load_tape")])
    assert "Duplicate" in str(excinfo.value)


def test_every_media_tool_declares_parameters_and_a_schema(registry: Any) -> None:
    schemas = registry.schemas()
    assert {schema["function"]["name"] for schema in schemas} == set(MEDIA_TOOL_NAMES)
    for schema in schemas:
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"


def test_the_three_registries_share_no_name() -> None:
    assert not (MEDIA_TOOL_NAMES & SETUP_TOOL_NAMES)
    assert not (MEDIA_TOOL_NAMES & READ_ONLY_TOOL_NAMES)
    assert not (SETUP_TOOL_NAMES & READ_ONLY_TOOL_NAMES)


# ---------------------------------------------------------------------------
# 2. Confirmation grades
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["load_tape", "unload_drive", "move_tape", "archive_path"])
def test_non_destructive_tools_are_yes_no(tool: str, registry: Any) -> None:
    """The grade is a property of the tool, not of what the model asked for."""
    definition = registry.get(tool)
    grade, required = definition.grade({}, {"barcode": FIRST_BARCODE})
    assert grade is ConfirmationGrade.YES_NO
    assert required is None


def test_format_is_graded_typed_with_the_barcode(registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    assert action.grade is ConfirmationGrade.TYPED
    assert action.required_response == FIRST_BARCODE
    assert action.destructive
    assert "irreversible" in action.preview.lower()
    assert FIRST_BARCODE in action.preview
    assert f"Type the barcode {FIRST_BARCODE}" in action.preview


def test_a_bare_yes_never_confirms_a_format(registry: Any, facade: Any) -> None:
    """THE MUTATION CHECK for the typed grade.

    Make ``verify_response`` treat a TYPED action like a YES_NO one and this fails.
    """
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    for answer in ("y", "yes", "Y", "YES", "ok", "", "   ", None):
        assert not verify_response(action, answer)
        assert registry.authorize(action, answer) is None


def test_the_wrong_barcode_never_confirms_a_format(registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    assert not verify_response(action, SECOND_BARCODE)
    assert verify_response(action, FIRST_BARCODE.lower()), "case is not the security boundary"


def test_a_required_response_of_yes_fails_closed(registry: Any, facade: Any) -> None:
    """A TYPED action whose required text is "y" is refused, not honoured.

    Otherwise a future tool could quietly downgrade the strongest confirmation in
    the system to a reflex one by picking the wrong required word.
    """
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    weakened = type(action)(
        tool=action.tool,
        arguments=action.arguments,
        preview=action.preview,
        grade=ConfirmationGrade.TYPED,
        plan=action.plan,
        required_response="y",
        token=action.token,
    )
    assert not verify_response(weakened, "y")


def test_typed_grade_with_no_required_text_fails_closed(registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    broken = type(action)(
        tool=action.tool,
        arguments=action.arguments,
        preview=action.preview,
        grade=ConfirmationGrade.TYPED,
        required_response=None,
    )
    assert not verify_response(broken, FIRST_BARCODE)
    assert not verify_response(broken, "y")


@pytest.mark.parametrize("answer", ["y", "yes", " Y ", "YES"])
def test_yes_answers_confirm_a_yes_no_action(answer: str, registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1)
    assert verify_response(action, answer)


@pytest.mark.parametrize("answer", ["", "   ", "n", "no", "ok", "sure", "yep", None])
def test_anything_else_refuses_a_yes_no_action(
    answer: str | None, registry: Any, facade: Any
) -> None:
    action = plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1)
    assert not verify_response(action, answer)


# ---------------------------------------------------------------------------
# 3. perform() cannot be reached without the confirmation
# ---------------------------------------------------------------------------


def test_perform_refuses_a_missing_authorization(registry: Any, facade: Any) -> None:
    """MUTATION CHECK: delete the ``authorization is None`` check -> this fails."""
    action = plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1)
    with pytest.raises(MediaNotAuthorizedError):
        registry.perform(action, facade, None)


def test_perform_refuses_an_authorization_for_another_action(registry: Any, facade: Any) -> None:
    """MUTATION CHECK: delete the ``action_key`` check -> this fails.

    The attack it blocks: confirm a harmless load, then replay that authorization
    against a format the operator never saw.
    """
    harmless = plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1)
    authorization = registry.authorize(harmless, "y")
    assert authorization is not None
    destructive = plan(registry, facade, "format_tape", barcode=SECOND_BARCODE)
    with pytest.raises(MediaNotAuthorizedError) as excinfo:
        registry.perform(destructive, facade, authorization)
    assert "different action" in str(excinfo.value)


def test_perform_refuses_a_downgraded_grade(registry: Any, facade: Any) -> None:
    """MUTATION CHECK: delete the grade check -> this fails.

    The attack: forge a YES_NO authorization for an action that is graded TYPED.
    """
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    forged = MediaAuthorization(action_key=action.key, grade=ConfirmationGrade.YES_NO, response="y")
    with pytest.raises(MediaNotAuthorizedError) as excinfo:
        registry.perform(action, facade, forged)
    assert "typed" in str(excinfo.value)


def test_perform_reverifies_the_typed_response(registry: Any, facade: Any) -> None:
    """MUTATION CHECK: delete the ``verify_response`` re-check in perform -> fails.

    The attack: a correctly-keyed, correctly-graded authorization carrying a
    response that does not actually satisfy the grade.
    """
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    forged = MediaAuthorization(action_key=action.key, grade=ConfirmationGrade.TYPED, response="y")
    with pytest.raises(MediaNotAuthorizedError) as excinfo:
        registry.perform(action, facade, forged)
    assert "does not satisfy" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. Robotics: load / unload / move
# ---------------------------------------------------------------------------


def test_load_preview_names_tape_slot_drive_and_serial(registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1)
    assert action.grade is ConfirmationGrade.YES_NO
    assert action.preview.startswith(f"Load {FIRST_BARCODE} from slot 1 into drive 1")
    assert "serial OBLADE_D02" in action.preview


def test_load_then_unload_round_trips(registry: Any, facade: Any, app_context: Any) -> None:
    loaded = run(
        registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1), "y"
    )
    assert loaded["driveId"] == 1
    inventory = app_context.library.inventory()
    assert str(inventory.drives[1].barcode) == FIRST_BARCODE

    unloaded = run(
        registry, facade, plan(registry, facade, "unload_drive", barcode=FIRST_BARCODE), "y"
    )
    assert unloaded["slotId"] == 1 or unloaded["slotId"] is not None
    assert app_context.library.inventory().drives[1].barcode is None


def test_load_picks_a_free_drive_and_says_so(registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "load_tape", barcode=FIRST_BARCODE)
    assert action.plan["driveChosenAutomatically"] is True
    assert "chosen because it is free" in action.preview


def test_unload_can_be_asked_for_by_drive(registry: Any, facade: Any) -> None:
    run(registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=2), "y")
    action = plan(registry, facade, "unload_drive", drive=2)
    assert action.arguments["barcode"] is None
    assert FIRST_BARCODE in action.preview


def test_move_preview_names_both_slots(registry: Any, facade: Any, app_context: Any) -> None:
    target = free_slot(app_context)
    action = plan(registry, facade, "move_tape", barcode=FIRST_BARCODE, slot=target)
    assert f"from slot 1 to slot {target}" in action.preview
    assert "nothing is exported" in action.preview


def test_move_actually_moves(registry: Any, facade: Any, app_context: Any) -> None:
    target = free_slot(app_context)
    run(
        registry,
        facade,
        plan(registry, facade, "move_tape", barcode=FIRST_BARCODE, slot=target),
        "y",
    )
    inventory = app_context.library.inventory()
    occupant = {int(slot.slot_id): str(slot.barcode) for slot in inventory.slots if slot.barcode}
    assert occupant.get(target) == FIRST_BARCODE
    assert 1 not in occupant


# ---------------------------------------------------------------------------
# 5. Ambiguity refuses BEFORE the prompt
# ---------------------------------------------------------------------------


def test_unknown_barcode_refuses_with_candidates(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "load_tape", barcode="VOL999L9")
    assert excinfo.value.code == "unknown_barcode"
    assert excinfo.value.candidates, "the operator is owed the tapes they might have meant"
    present = set(facade.library_state()["barcodes"])
    assert set(excinfo.value.candidates) <= present, "every candidate must be a real tape"
    # Ranked by evidence: VOL999L9 shares the VOL9 stem with nothing, so the
    # prefix bucket is empty and the fallback is the library's own barcodes.
    assert excinfo.value.candidates[0] in present


def test_loading_into_an_occupied_drive_refuses(registry: Any, facade: Any) -> None:
    run(registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1), "y")
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "load_tape", barcode=SECOND_BARCODE, drive=1)
    assert excinfo.value.code == "drive_occupied"
    assert FIRST_BARCODE in str(excinfo.value)


def test_loading_an_already_loaded_tape_refuses(registry: Any, facade: Any) -> None:
    run(registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=1), "y")
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "load_tape", barcode=FIRST_BARCODE)
    assert excinfo.value.code == "already_loaded"


def test_unloading_a_tape_that_is_in_a_slot_refuses(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "unload_drive", barcode=FIRST_BARCODE)
    assert excinfo.value.code == "not_loaded"


def test_unload_without_a_target_refuses_rather_than_guessing(registry: Any, facade: Any) -> None:
    """Two drives loaded, no target named: a guess here unloads the wrong tape."""
    run(registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=0), "y")
    run(registry, facade, plan(registry, facade, "load_tape", barcode=SECOND_BARCODE, drive=1), "y")
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "unload_drive")
    assert excinfo.value.code == "unload_target_unspecified"
    assert len(excinfo.value.candidates) == 2


def test_unload_refuses_when_drive_and_barcode_disagree(registry: Any, facade: Any) -> None:
    run(registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=0), "y")
    run(registry, facade, plan(registry, facade, "load_tape", barcode=SECOND_BARCODE, drive=1), "y")
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "unload_drive", barcode=FIRST_BARCODE, drive=1)
    assert excinfo.value.code == "drive_barcode_mismatch"


def test_unload_refuses_while_ltfs_is_mounted(registry: Any, facade: Any, app_context: Any) -> None:
    """The project non-negotiable, enforced before the operator is asked.

    MUTATION CHECK: remove ``_require_unmounted`` from ``_resolve_unload`` and this
    test fails — the plan succeeds and a mounted volume is offered for unload.
    """
    formatted_tape(app_context, FIRST_BARCODE)
    run(registry, facade, plan(registry, facade, "load_tape", barcode=FIRST_BARCODE, drive=0), "y")
    app_context.ltfs.mount(FIRST_BARCODE, MountMode.READ_WRITE)
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "unload_drive", barcode=FIRST_BARCODE)
    assert excinfo.value.code == "drive_mounted"


def test_moving_to_an_occupied_slot_refuses(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "move_tape", barcode=FIRST_BARCODE, slot=2)
    assert excinfo.value.code == "slot_occupied"
    assert SECOND_BARCODE in str(excinfo.value)


def test_moving_to_a_nonexistent_slot_refuses(registry: Any, facade: Any) -> None:
    """The import/export element is not a destination the assistant offers."""
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "move_tape", barcode=FIRST_BARCODE, slot=9999)
    assert excinfo.value.code == "unknown_slot"
    assert "import/export" in str(excinfo.value)


def test_moving_to_the_same_slot_refuses(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "move_tape", barcode=FIRST_BARCODE, slot=1)
    assert excinfo.value.code == "same_slot"


def test_a_missing_barcode_refuses_rather_than_defaulting(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "load_tape", barcode=None)
    assert excinfo.value.code == "missing_barcode"


# ---------------------------------------------------------------------------
# 6. Format: the two-phase flow is used, never bypassed
# ---------------------------------------------------------------------------


def test_planning_a_format_mints_a_real_one_time_token(
    registry: Any, facade: Any, app_context: Any
) -> None:
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    assert action.token, "the dry run must mint a token"
    assert app_context.catalog.get_safety_token(action.token) is not None
    assert 0 < action.plan["tokenTtlSeconds"] <= 300
    assert "token" not in action.arguments, "a live authorization must not enter the audit args"
    assert action.token not in action.preview, "the token is not shown; the barcode is"


def test_a_format_without_the_dry_run_fails_rather_than_skipping_it(
    registry: Any, facade: Any
) -> None:
    """THE MUTATION CHECK for the two-phase flow.

    Strip the token out of a planned action — exactly what "skip the dry run" would
    produce — and the tool must FAIL, not proceed with weaker checks. Delete the
    token check in ``_format_tape`` and this still fails (the service refuses an
    unknown token), which is the point: there is no path with no token.
    """
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    tokenless = type(action)(
        tool=action.tool,
        arguments=action.arguments,
        preview=action.preview,
        grade=action.grade,
        plan={key: value for key, value in action.plan.items() if key != "token"},
        required_response=action.required_response,
        token=None,
    )
    authorization = registry.authorize(tokenless, FIRST_BARCODE)
    with pytest.raises(MediaRefusedError) as excinfo:
        registry.perform(tokenless, facade, authorization)
    assert excinfo.value.code == "missing_safety_token"


def test_a_forged_token_does_not_format(registry: Any, facade: Any) -> None:
    from openblade.assistant.errors import MediaOperationFailedError

    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    forged = type(action)(
        tool=action.tool,
        arguments=action.arguments,
        preview=action.preview,
        grade=action.grade,
        plan=action.plan,
        required_response=action.required_response,
        token="not-a-real-token",
    )
    authorization = registry.authorize(forged, FIRST_BARCODE)
    with pytest.raises(MediaOperationFailedError):
        registry.perform(forged, facade, authorization)


def test_a_confirmed_format_runs_and_consumes_the_token(
    registry: Any, facade: Any, app_context: Any
) -> None:
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    result = run(registry, facade, action, FIRST_BARCODE)
    assert result["success"] is True
    assert result["tokenConsumed"] is True
    assert app_context.catalog.get_safety_token(action.token) is None, "one-time means one time"


def test_the_format_preview_states_what_is_lost(registry: Any, facade: Any) -> None:
    action = plan(registry, facade, "format_tape", barcode=FIRST_BARCODE)
    assert "Everything on the cartridge is destroyed" in action.preview
    assert "WORM" in action.preview
    assert "LTFS label" in action.preview
    assert "no undo" in action.preview


def test_formatting_an_unknown_barcode_refuses_without_minting_a_token(
    app_context: Any, registry: Any
) -> None:
    """A refusal must not leave a live format authorization sitting in the database.

    Asserted on the service, not on the outcome: the dry run is never reached, so
    there is no token value to go looking for afterwards.
    """
    calls: list[str] = []
    original = app_context.format_service.dry_run

    def spy(barcode: str) -> Any:
        calls.append(barcode)
        return original(barcode)

    app_context.format_service.dry_run = spy  # type: ignore[method-assign]
    spied = media_facade_for(app_context)
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, spied, "format_tape", barcode="VOL999L9")
    assert excinfo.value.code == "unknown_barcode"
    assert calls == []
    # And the guard is not vacuous: a known barcode does reach the dry run.
    plan(registry, spied, "format_tape", barcode=FIRST_BARCODE)
    assert calls == [FIRST_BARCODE]


# ---------------------------------------------------------------------------
# 7. Archive and restore
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_tree(tmp_path: Path) -> Path:
    source = tmp_path / "photos"
    source.mkdir()
    (source / "one.raw").write_bytes(b"first file contents")
    (source / "two.raw").write_bytes(b"second file contents, longer")
    return source


def test_archive_refuses_an_unknown_volume_group(
    registry: Any, facade: Any, sample_tree: Path
) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "archive_path", path=str(sample_tree), volume_group="nope")
    assert excinfo.value.code == "unknown_volume_group"


def test_archive_refuses_a_relative_path(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "archive_path", path="photos", volume_group="any")
    assert excinfo.value.code == "relative_source path"


def test_archive_refuses_a_missing_source(
    registry: Any, facade: Any, app_context: Any, tmp_path: Path
) -> None:
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(
            registry,
            facade,
            "archive_path",
            path=str(tmp_path / "absent"),
            volume_group="pool",
        )
    assert excinfo.value.code == "source_not_found"


def test_archive_runs_and_reports_verified_counts(
    registry: Any, facade: Any, app_context: Any, sample_tree: Path
) -> None:
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    action = plan(registry, facade, "archive_path", path=str(sample_tree), volume_group="pool")
    assert action.grade is ConfirmationGrade.YES_NO
    assert "2 file(s)" in action.preview
    assert "can take minutes" in action.preview

    result = run(registry, facade, action, "y")
    assert result["filesArchived"] == 2
    assert result["filesExpected"] == 2
    assert result["allFilesInCatalog"] is True
    assert result["bytesArchived"] == 47
    assert result["tapes"]
    assert result["jobId"]


def test_restore_round_trips_byte_identically(
    registry: Any, facade: Any, app_context: Any, sample_tree: Path, tmp_path: Path
) -> None:
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    run(
        registry,
        facade,
        plan(registry, facade, "archive_path", path=str(sample_tree), volume_group="pool"),
        "y",
    )
    destination = tmp_path / "restored.raw"
    action = plan(registry, facade, "restore_path", path="/pool/one.raw", dest=str(destination))
    assert action.grade is ConfirmationGrade.YES_NO, "nothing is overwritten"
    assert "Nothing is overwritten" in action.preview
    result = run(registry, facade, action, "y")
    assert result["checksumVerified"] is True
    assert result["sizeMatches"] is True
    assert result["overwrote"] is False
    assert destination.read_bytes() == (sample_tree / "one.raw").read_bytes()


def test_restore_over_an_existing_file_demands_the_typed_word(
    registry: Any, facade: Any, app_context: Any, sample_tree: Path, tmp_path: Path
) -> None:
    """THE MUTATION CHECK for the restore grade.

    Make ``_grade_restore`` always return YES_NO and this fails: an overwrite would
    be confirmable with a reflex "y".
    """
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    run(
        registry,
        facade,
        plan(registry, facade, "archive_path", path=str(sample_tree), volume_group="pool"),
        "y",
    )
    destination = tmp_path / "existing.raw"
    destination.write_bytes(b"something the operator still wants")
    action = plan(registry, facade, "restore_path", path="/pool/one.raw", dest=str(destination))
    assert action.grade is ConfirmationGrade.TYPED
    assert action.required_response == OVERWRITE_WORD
    assert "ALREADY EXISTS" in action.preview
    assert "will be OVERWRITTEN" in action.preview
    assert not verify_response(action, "y")
    assert verify_response(action, OVERWRITE_WORD)

    result = run(registry, facade, action, OVERWRITE_WORD)
    assert result["overwrote"] is True
    assert destination.read_bytes() == (sample_tree / "one.raw").read_bytes()


def test_restore_into_a_directory_checks_the_file_it_will_actually_write(
    registry: Any, facade: Any, app_context: Any, sample_tree: Path, tmp_path: Path
) -> None:
    """A directory destination means ``<dir>/<name>`` — and that is what is checked.

    Get this wrong and the overwrite guard inspects a path nothing writes to, so
    the strongest confirmation in the system guards the wrong file.
    """
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    run(
        registry,
        facade,
        plan(registry, facade, "archive_path", path=str(sample_tree), volume_group="pool"),
        "y",
    )
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "one.raw").write_bytes(b"already here")
    action = plan(registry, facade, "restore_path", path="/pool/one.raw", dest=str(outdir))
    assert action.plan["destinationPath"] == str(outdir / "one.raw")
    assert action.grade is ConfirmationGrade.TYPED


def test_restore_refuses_an_unknown_catalog_path(registry: Any, facade: Any) -> None:
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "restore_path", path="/nowhere/file.raw", dest="/tmp")
    assert excinfo.value.code == "unknown_catalog_path"


def test_restore_refuses_a_destination_directory_that_does_not_exist(
    registry: Any, facade: Any, app_context: Any, sample_tree: Path, tmp_path: Path
) -> None:
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    run(
        registry,
        facade,
        plan(registry, facade, "archive_path", path=str(sample_tree), volume_group="pool"),
        "y",
    )
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(
            registry,
            facade,
            "restore_path",
            path="/pool/one.raw",
            dest=str(tmp_path / "absent" / "file.raw"),
        )
    assert excinfo.value.code == "destination_missing"


def test_archive_refuses_an_oversized_tree(
    registry: Any, facade: Any, app_context: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_facade_module, "MAX_ARCHIVE_FILES", 1)
    app_context.catalog.create_volume_group("pool")
    formatted_tape(app_context, FIRST_BARCODE)
    source = tmp_path / "many"
    source.mkdir()
    (source / "a").write_bytes(b"a")
    (source / "b").write_bytes(b"b")
    with pytest.raises(MediaRefusedError) as excinfo:
        plan(registry, facade, "archive_path", path=str(source), volume_group="pool")
    assert excinfo.value.code == "too_many_files"
    assert "openblade archive" in str(excinfo.value)
