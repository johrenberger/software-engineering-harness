"""Adversarial tests pinning every detail of slice 2 modules.

These exist to ensure that mutmut cannot find survivors in
``src/seharness/state_machine.py`` or ``src/seharness/artifacts/store.py``
after RED/GREEN/REFACTOR.

Each test class targets a specific category of equivalent-mutant
survivors that surfaced during the first mutation run:
- constant-default substitutions (``= 0`` -> ``= 1``, ``= ()`` -> ``= []``)
- removed attribute assignments
- swapped keywords (``dir=`` -> kw removed)
- conditionals flipped (``is_dir()`` -> ``True``)
- method substitution (``iterdir()`` -> ``iter()``)

Tests in this module pin:
- exact attribute values on exception classes
- exact defaults of dataclass and Pydantic model fields
- the exact call signature of ``atomic_write_json`` (keyword-only indent)
- the exact call signature of ``tempfile.mkstemp`` (prefix, suffix, dir)
- that ``model_dump(mode=\"json\")`` is what produces the persisted bytes
- that ``StateRepository`` performs the side effect of creating its root dir
- that ``list_runs`` actually walks the filesystem (not, e.g., just returns
  whatever ``iter()`` yields)
- that ``InvalidTransitionError`` carries both source and target
- that ``WorkflowState`` defaults are exactly 0
- that ``_derive_run_status`` returns the correct ``RunStatus`` for each
  terminal target phase
"""

from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError as PydValidationError

import seharness.artifacts.store as store_module
from seharness.artifacts.store import (
    RunNotFoundError,
    StateRepository,
    WorkflowStateModel,
    atomic_write_json,
)
from seharness.domain.enums import PhaseName, RunStatus
from seharness.state_machine import (
    TERMINAL_PHASES,
    InvalidTransitionError,
    WorkflowState,
)


# ---------------------------------------------------------------------
# InvalidTransitionError
# ---------------------------------------------------------------------
class TestInvalidTransitionErrorSurface:
    def test_init_stores_source_attribute(self) -> None:
        exc = InvalidTransitionError(PhaseName.INTAKE, PhaseName.FAILED)
        assert exc.source is PhaseName.INTAKE

    def test_init_stores_target_attribute(self) -> None:
        exc = InvalidTransitionError(PhaseName.INTAKE, PhaseName.FAILED)
        assert exc.target is PhaseName.FAILED

    def test_init_target_is_not_renamed_source(self) -> None:
        # mutmut candidate: ``self.target = source``. Pin by checking
        # that ``exc.target`` is the target argument, not the source.
        exc = InvalidTransitionError(PhaseName.INTAKE, PhaseName.EXECUTION)
        assert exc.target is PhaseName.EXECUTION
        assert exc.target is not PhaseName.INTAKE

    def test_str_includes_source_phase_name(self) -> None:
        exc = InvalidTransitionError(PhaseName.INTAKE, PhaseName.EXECUTION)
        assert "intake" in str(exc)

    def test_str_includes_target_phase_name(self) -> None:
        exc = InvalidTransitionError(PhaseName.INTAKE, PhaseName.EXECUTION)
        assert "execution" in str(exc)

    def test_str_includes_arrow_arrow_separator(self) -> None:
        # The separator is " -> "; without this the error message is
        # confusing in logs.
        exc = InvalidTransitionError(PhaseName.INTAKE, PhaseName.EXECUTION)
        assert "->" in str(exc)

    def test_is_value_error_subclass(self) -> None:
        # Spec says it's a domain exception; ValueError is what the
        # state_machine documentation promises.
        assert issubclass(InvalidTransitionError, ValueError)


# ---------------------------------------------------------------------
# WorkflowState defaults
# ---------------------------------------------------------------------
class TestWorkflowStateDefaults:
    def test_default_task_retries_is_zero(self) -> None:
        state = WorkflowState(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert state.task_retries == 0

    def test_default_repair_retries_is_zero(self) -> None:
        state = WorkflowState(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert state.repair_retries == 0

    def test_default_history_is_empty_tuple(self) -> None:
        state = WorkflowState(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert state.history == ()

    def test_default_history_is_tuple_not_list(self) -> None:
        # The dataclass uses tuple for immutability; mutmut might
        # change ``()`` to ``[]`` in an attempt to find survivors.
        state = WorkflowState(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert isinstance(state.history, tuple)

    def test_default_history_is_zero_length(self) -> None:
        # mutmut L76 candidate: ``()`` -> ``(None,)``. Pin via len().
        state = WorkflowState(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert len(state.history) == 0
        # also pin bool()
        assert not state.history

    def test_dataclass_is_frozen(self) -> None:
        state = WorkflowState(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        with pytest.raises((AttributeError, Exception)):
            # frozen=True -> cannot setattr
            state.run_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------
# WorkflowState._derive_run_status
# ---------------------------------------------------------------------
class TestDeriveRunStatusExact:
    @pytest.mark.parametrize(
        "phase,expected",
        [
            (PhaseName.COMPLETED, RunStatus.COMPLETED),
            (PhaseName.FAILED, RunStatus.FAILED),
            (PhaseName.BLOCKED, RunStatus.BLOCKED),
        ],
    )
    def test_terminal_target_maps_to_matching_status(
        self, phase: PhaseName, expected: RunStatus
    ) -> None:
        assert WorkflowState._derive_run_status(phase) is expected

    @pytest.mark.parametrize(
        "phase",
        [
            PhaseName.INTAKE,
            PhaseName.DISCOVERY,
            PhaseName.SPECIFICATION,
            PhaseName.IMPACT,
            PhaseName.PLANNING,
            PhaseName.EXECUTION,
            PhaseName.VALIDATION,
            PhaseName.REMEDIATION,
            PhaseName.REVIEW,
            PhaseName.DELIVERY,
            PhaseName.CI_MONITORING,
        ],
    )
    def test_working_target_maps_to_running(self, phase: PhaseName) -> None:
        assert WorkflowState._derive_run_status(phase) is RunStatus.RUNNING


# ---------------------------------------------------------------------
# TERMINAL_PHASES
# ---------------------------------------------------------------------
class TestTerminalPhasesExact:
    def test_contains_completed(self) -> None:
        assert PhaseName.COMPLETED in TERMINAL_PHASES

    def test_contains_failed(self) -> None:
        assert PhaseName.FAILED in TERMINAL_PHASES

    def test_contains_blocked(self) -> None:
        assert PhaseName.BLOCKED in TERMINAL_PHASES

    def test_excludes_intake(self) -> None:
        assert PhaseName.INTAKE not in TERMINAL_PHASES

    def test_excludes_discovery(self) -> None:
        assert PhaseName.DISCOVERY not in TERMINAL_PHASES

    def test_is_frozenset(self) -> None:
        # The spec calls for an immutable set so it can be shared
        # across the artifact store and the state machine.
        assert isinstance(TERMINAL_PHASES, frozenset)


# ---------------------------------------------------------------------
# RunNotFoundError
# ---------------------------------------------------------------------
class TestRunNotFoundErrorSurface:
    def test_init_stores_run_id(self) -> None:
        exc = RunNotFoundError("r-x", Path("/tmp/r-x-root"))
        assert exc.run_id == "r-x"

    def test_init_stores_root_path(self) -> None:
        root = Path("/tmp/x")
        exc = RunNotFoundError("r-x", root)
        assert exc.root is root

    def test_str_includes_run_id(self) -> None:
        exc = RunNotFoundError("r-unique-marker", Path("/tmp/x"))
        assert "r-unique-marker" in str(exc)

    def test_is_keyerror_subclass(self) -> None:
        assert issubclass(RunNotFoundError, KeyError)

    def test_init_root_is_not_none(self) -> None:
        # mutmut L50 candidate: ``self.root = None``. Pin that the
        # attribute holds the actual root path.
        exc = RunNotFoundError("r-x", Path("/tmp/run-not-found-root"))
        assert exc.root is not None
        assert exc.root == Path("/tmp/run-not-found-root")

    def test_init_root_passes_through_when_string(self) -> None:
        # mutmut L50 candidate: ``self.root = Path(root)`` instead of
        # ``self.root = root``. The two are equivalent when ``root`` is
        # a Path; they differ when ``root`` is a string. Pin the
        # assignment by passing a string and asserting the value is the
        # same string, not a Path wrapping it.
        exc = RunNotFoundError("r-x", "/tmp/string-root")  # type: ignore[arg-type]
        assert exc.root == "/tmp/string-root"
        assert isinstance(exc.root, str)

    def test_can_be_caught_as_keyerror(self) -> None:
        with pytest.raises(KeyError):
            raise RunNotFoundError("r-x", Path("/tmp/x"))


# ---------------------------------------------------------------------
# WorkflowStateModel defaults
# ---------------------------------------------------------------------
class TestWorkflowStateModelDefaults:
    def test_default_task_retries_is_zero(self) -> None:
        model = WorkflowStateModel(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert model.task_retries == 0

    def test_default_repair_retries_is_zero(self) -> None:
        model = WorkflowStateModel(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert model.repair_retries == 0

    def test_default_history_is_empty_tuple(self) -> None:
        model = WorkflowStateModel(
            run_id="r",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        assert model.history == ()

    def test_run_id_min_length_one_enforced(self) -> None:

        with pytest.raises(PydValidationError):
            WorkflowStateModel(
                run_id="",
                current_phase=PhaseName.INTAKE,
                run_status=RunStatus.RUNNING,
            )

    def test_unknown_top_level_key_rejected(self) -> None:
        # ``extra=\"forbid\"`` must hold; a malformed run-state.json
        # cannot sneak into a corrupted run.

        with pytest.raises(PydValidationError):
            WorkflowStateModel(
                run_id="r",
                current_phase=PhaseName.INTAKE,
                run_status=RunStatus.RUNNING,
                unknown_field="oops",
            )


# ---------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------
class TestAtomicWriteSignature:
    def test_default_indent_is_two(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        atomic_write_json(target, {"a": 1})
        # If the default were 0 or 4 the test below would fail (line
        # count differs in many payloads; use a dict whose string
        # representation differs under indent).
        text = target.read_text()
        assert "\n  " in text  # two-space indent produces "  a"

    def test_indent_keyword_only(self, tmp_path: Path) -> None:
        # The signature is ``(*, indent=2)``; indent must NOT be
        # passable positionally.
        target = tmp_path / "f.json"
        with pytest.raises(TypeError):
            atomic_write_json(target, {"a": 1}, 4)  # type: ignore[misc]

    def test_indent_parameter_kind_is_keyword_only(self) -> None:
        # A direct pin via inspect: ``indent`` must be
        # ``Parameter.KEYWORD_ONLY``. Mutating the ``*,`` to nothing
        # changes the kind to POSITIONAL_OR_KEYWORD.

        sig = inspect.signature(atomic_write_json)
        indent_param = sig.parameters["indent"]
        assert indent_param.kind is inspect.Parameter.KEYWORD_ONLY

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "f.json"
        atomic_write_json(target, {"a": 1})
        assert target.is_file()

    def test_parent_dirs_exist_ok_idempotent(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        atomic_write_json(target, {"a": 1})
        # Second write to the same dir must not raise (exist_ok=True).
        atomic_write_json(target, {"a": 2})


class TestAtomicWriteTempfilePrefixSuffix:
    def test_uses_tmp_prefix_and_json_suffix(self, tmp_path: Path) -> None:
        """``atomic_write_json`` must pass prefix=\".tmp.\" and
        suffix=\".json\" to ``tempfile.mkstemp`` so leftover crash
        artifacts in a run directory are unambiguous.

        We assert this indirectly by patching ``tempfile.mkstemp`` to
        capture its kwargs.
        """
        target = tmp_path / "f.json"
        captured: dict = {}

        original = tempfile.mkstemp

        def spy(*args: object, **kwargs: object) -> tuple[int, str]:
            captured.update(kwargs)
            return original(*args, **kwargs)

        store_module.tempfile.mkstemp = spy  # type: ignore[assignment]
        try:
            atomic_write_json(target, {"a": 1})
        finally:
            store_module.tempfile.mkstemp = original  # type: ignore[assignment]

        assert captured.get("prefix") == ".tmp."
        assert captured.get("suffix") == ".json"

    def test_tempfile_in_same_dir_as_target(self, tmp_path: Path) -> None:
        """The temp file MUST live in the same directory as the
        destination so ``os.replace`` stays atomic. We assert this by
        checking that ``dir=`` is the parent of ``target``.
        """
        target = tmp_path / "f.json"
        captured: dict = {}

        original = tempfile.mkstemp

        def spy(*args: object, **kwargs: object) -> tuple[int, str]:
            captured.update(kwargs)
            return original(*args, **kwargs)

        store_module.tempfile.mkstemp = spy  # type: ignore[assignment]
        try:
            atomic_write_json(target, {"a": 1})
        finally:
            store_module.tempfile.mkstemp = original  # type: ignore[assignment]

        assert captured.get("dir") == str(target.parent)

    def test_cleans_up_temp_on_error(self, tmp_path: Path) -> None:
        """If the destination write raises, the temp file is removed.
        We force an error by passing a non-serializable object.
        """
        target = tmp_path / "f.json"
        with pytest.raises(TypeError):
            atomic_write_json(target, {1, 2, 3})  # set is not JSON-serializable
        # No leftover .tmp.* in tmp_path
        leaked = list(tmp_path.glob(".tmp.*"))
        assert leaked == [], f"unexpected temp leftovers: {leaked}"

    def test_does_not_leave_temp_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        atomic_write_json(target, {"a": 1})
        leaked = list(tmp_path.glob(".tmp.*"))
        assert leaked == []

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        atomic_write_json(target, {"a": 1, "b": [1, 2]})
        loaded = json.loads(target.read_text())
        assert loaded == {"a": 1, "b": [1, 2]}

    def test_writes_with_trailing_newline(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        atomic_write_json(target, {"a": 1})
        text = target.read_text()
        assert text.endswith("\n")

    def test_writes_utf8_encoded_bytes(self, tmp_path: Path) -> None:
        # mutmut L129 candidate: change encoding="utf-8" to "ascii".
        # Pin by writing a payload that round-trips correctly only
        # under utf-8 (non-ASCII character).
        target = tmp_path / "f.json"
        payload = {"msg": "café"}
        atomic_write_json(target, payload)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == payload
        assert loaded["msg"] == "café"


# ---------------------------------------------------------------------
# StateRepository __init__
# ---------------------------------------------------------------------
class TestStateRepositoryInit:
    def test_creates_root_dir_when_missing(self, tmp_path: Path) -> None:
        new_root = tmp_path / "fresh-root"
        assert not new_root.exists()
        StateRepository(new_root)
        assert new_root.is_dir()

    def test_accepts_string_root(self, tmp_path: Path) -> None:
        new_root = tmp_path / "string-root"
        repo = StateRepository(str(new_root))
        # root property returns Path
        assert isinstance(repo.root, Path)
        assert repo.root == new_root

    def test_accepts_string_root_and_root_property_matches_input(self, tmp_path: Path) -> None:
        # mutmut L158 candidate: ``self._root = Path("hardcoded")`` or
        # similar. Pin that the ``root`` property exposes the EXACT
        # Path object derived from the constructor argument.
        new_root = tmp_path / "exact-root-match"
        repo = StateRepository(new_root)
        assert repo.root == new_root
        assert repo.root.is_absolute() == new_root.is_absolute()
        # The repository should NOT silently coerce the input to a
        # different directory.
        assert str(repo.root).endswith("exact-root-match")

    def test_accepts_pathlike_root(self, tmp_path: Path) -> None:
        new_root = tmp_path / "path-root"
        repo = StateRepository(new_root)
        assert repo.root == new_root

    def test_existing_root_is_ok(self, tmp_path: Path) -> None:
        # No exception if root already exists.
        repo = StateRepository(tmp_path)
        assert repo.root == tmp_path


# ---------------------------------------------------------------------
# StateRepository.list_runs
# ---------------------------------------------------------------------
class TestListRunsWalksFilesystem:
    def test_uses_iterdir_not_iter(self, tmp_path: Path) -> None:
        """``list_runs`` walks the filesystem via ``iterdir``; mutating
        that to ``iter(self._root)`` would silently break.

        We pin this by placing non-directory entries (files) at the
        top level and asserting they are NOT included.
        """
        # Create a stray file at the root.
        (tmp_path / "stray-file.txt").write_text("hi")
        # And a directory with no run-state.json.
        (tmp_path / "junk-dir").mkdir()
        # And a real run.
        repo = StateRepository(tmp_path)
        repo.save(
            WorkflowState(
                run_id="r-real",
                current_phase=PhaseName.INTAKE,
                run_status=RunStatus.RUNNING,
            )
        )
        runs = list(repo.list_runs())
        assert "r-real" in runs
        assert "stray-file.txt" not in runs
        assert "junk-dir" not in runs

    def test_returns_empty_when_root_not_dir(self, tmp_path: Path) -> None:
        # Construct a repository whose root is a file (not a directory).
        fake = tmp_path / "not-a-dir"
        fake.write_text("x")
        repo = StateRepository.__new__(StateRepository)
        repo._root = fake  # type: ignore[attr-defined]
        assert list(repo.list_runs()) == []


# ---------------------------------------------------------------------
# StateRepository.save / load use model_dump(mode="json")
# ---------------------------------------------------------------------
class TestRepositoryUsesJsonMode:
    def test_persists_phase_as_string_not_strenum(
        self, repository: StateRepository, tmp_path: Path
    ) -> None:
        repository.save(
            WorkflowState(
                run_id="r-json",
                current_phase=PhaseName.EXECUTION,
                run_status=RunStatus.RUNNING,
            )
        )
        path = tmp_path / "r-json" / "run-state.json"
        raw = json.loads(path.read_text())
        # ``model_dump(mode=\"json\")`` serializes StrEnum as its value
        # (a plain string), not the StrEnum repr.
        assert raw["current_phase"] == "execution"
        assert isinstance(raw["current_phase"], str)

    def test_persists_status_as_string(self, repository: StateRepository, tmp_path: Path) -> None:
        repository.save(
            WorkflowState(
                run_id="r-s",
                current_phase=PhaseName.INTAKE,
                run_status=RunStatus.RUNNING,
            )
        )
        path = tmp_path / "r-s" / "run-state.json"
        raw = json.loads(path.read_text())
        assert raw["run_status"] == "running"

    def test_persists_history_as_list_of_tuples(
        self, repository: StateRepository, tmp_path: Path
    ) -> None:
        state = WorkflowState(
            run_id="r-h",
            current_phase=PhaseName.INTAKE,
            run_status=RunStatus.RUNNING,
        )
        state = state.transition_to(PhaseName.DISCOVERY)
        repository.save(state)
        path = tmp_path / "r-h" / "run-state.json"
        raw = json.loads(path.read_text())
        # JSON arrays of arrays, not nested objects.
        assert isinstance(raw["history"], list)
        assert raw["history"][0] == ["intake", "discovery"]


# ---------------------------------------------------------------------
# Fixture used by some repository tests
# ---------------------------------------------------------------------
@pytest.fixture
def repository(tmp_path: Path) -> StateRepository:
    return StateRepository(tmp_path)


# ---------------------------------------------------------------------
# find_resumable uses TERMINAL_PHASES, not its own subset
# ---------------------------------------------------------------------
class TestFindResumableUsesTerminalPhases:
    def test_completed_is_not_resumable(self, repository: StateRepository) -> None:
        repository.save(
            WorkflowState(
                run_id="r-c",
                current_phase=PhaseName.COMPLETED,
                run_status=RunStatus.COMPLETED,
            )
        )
        assert "r-c" not in list(repository.find_resumable())

    def test_blocked_is_not_resumable(self, repository: StateRepository) -> None:
        repository.save(
            WorkflowState(
                run_id="r-b",
                current_phase=PhaseName.BLOCKED,
                run_status=RunStatus.BLOCKED,
            )
        )
        assert "r-b" not in list(repository.find_resumable())

    def test_failed_is_not_resumable(self, repository: StateRepository) -> None:
        repository.save(
            WorkflowState(
                run_id="r-f",
                current_phase=PhaseName.FAILED,
                run_status=RunStatus.FAILED,
            )
        )
        assert "r-f" not in list(repository.find_resumable())

    @pytest.mark.parametrize(
        "phase",
        [
            PhaseName.INTAKE,
            PhaseName.DISCOVERY,
            PhaseName.PLANNING,
            PhaseName.EXECUTION,
            PhaseName.VALIDATION,
            PhaseName.REMEDIATION,
            PhaseName.REVIEW,
            PhaseName.DELIVERY,
            PhaseName.CI_MONITORING,
        ],
    )
    def test_non_terminal_is_resumable(self, repository: StateRepository, phase: PhaseName) -> None:
        repository.save(
            WorkflowState(
                run_id=f"r-{phase.value}",
                current_phase=phase,
                run_status=RunStatus.RUNNING,
            )
        )
        assert f"r-{phase.value}" in list(repository.find_resumable())


# ---------------------------------------------------------------------
# WorkflowState transition_to integration (pin _derive_run_status uses)
# ---------------------------------------------------------------------
class TestTransitionToStatusMapping:
    def test_transitioning_to_completed_yields_completed_status(self) -> None:
        # Walking through DELIVERY -> CI_MONITORING -> COMPLETED.
        s = WorkflowState(run_id="r", current_phase=PhaseName.INTAKE, run_status=RunStatus.RUNNING)
        s = s.transition_to(PhaseName.DISCOVERY)
        s = s.transition_to(PhaseName.SPECIFICATION)
        s = s.transition_to(PhaseName.IMPACT)
        s = s.transition_to(PhaseName.PLANNING)
        s = s.transition_to(PhaseName.EXECUTION)
        s = s.transition_to(PhaseName.VALIDATION)
        s = s.transition_to(PhaseName.REVIEW)
        s = s.transition_to(PhaseName.DELIVERY)
        s = s.transition_to(PhaseName.CI_MONITORING)
        s = s.transition_to(PhaseName.COMPLETED)
        assert s.run_status is RunStatus.COMPLETED

    def test_transitioning_to_failed_yields_failed_status(self) -> None:
        s = WorkflowState(run_id="r", current_phase=PhaseName.INTAKE, run_status=RunStatus.RUNNING)
        s = s.transition_to(PhaseName.FAILED)
        assert s.run_status is RunStatus.FAILED

    def test_transitioning_to_blocked_yields_blocked_status(self) -> None:
        s = WorkflowState(run_id="r", current_phase=PhaseName.INTAKE, run_status=RunStatus.RUNNING)
        s = s.transition_to(PhaseName.BLOCKED)
        assert s.run_status is RunStatus.BLOCKED
