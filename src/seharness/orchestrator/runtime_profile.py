"""Runtime profile adapter validation (WP2 / story WP2.1).

Production deployments must refuse to start when critical adapters
resolve to a stub or test double. Development and test profiles
allow stubs with a startup warning so notebook + local-iteration
workflows are not broken.

The check is intentionally simple: it enumerates the wired
adapters on an ``Orchestrator``-like object and rejects any whose
class name contains a known stub marker (``"Stub"``,
``"Fake"``, ``"Noop"``, ``"InMemory"``) when the profile is
``PRODUCTION``. The marker list is short on purpose — production
adapters must be explicitly typed concrete classes; we do not
introspect duck-type signatures here.

For test profiles, the check is a no-op (returns the empty list).
For development profiles, the check returns the list of stub
adapter class names so the caller can log a single startup
warning instead of raising.

The check is invoked from ``Orchestrator.__init__`` after the
adapter slots are wired. See ``tests/unit/orchestrator/test_runtime_profile.py``
for the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from seharness.config import RuntimeProfile
from seharness.exceptions import ConfigurationError


#: Class-name substrings that mark an adapter as a stub / fake / no-op.
#: Kept as a module-level constant so it is easy to extend and so the
#: mutmut gates don't see it as a magic-string worth killing.
STUB_CLASS_MARKERS: tuple[str, ...] = (
    "Stub",
    "Fake",
    "Noop",
    "NoOp",
    "InMemory",
)


@dataclass(frozen=True)
class RuntimeProfileDiagnostic:
    """Outcome of a runtime-profile adapter check.

    ``stub_adapters`` lists the (slot, class_name) pairs that look
    like stubs. Empty for ``PRODUCTION`` (because any stub raises
    :class:`ConfigurationError`) and for ``TEST`` (we don't bother
    enumerating). Non-empty only for ``DEVELOPMENT``, where the
    caller is expected to log it as a single startup warning.
    """

    profile: RuntimeProfile
    stub_adapters: tuple[tuple[str, str], ...]


class _AdapterSlot(Protocol):
    """An attribute on the orchestrator that holds an adapter.

    We don't introspect the adapter's interface here — we just
    need to know its class name. The slots are passed in as a
    mapping of name → adapter value so the validator is decoupled
    from ``Orchestrator`` itself.
    """

    def __class__(self) -> type: ...


def _classify_adapter(slot_name: str, adapter: object) -> tuple[str, str] | None:
    """Return (slot, class_name) if ``adapter`` looks like a stub.

    Returns ``None`` for adapters that aren't class-named (e.g.
    ``None`` for an unwired slot) and for adapters whose class name
    does not contain any :data:`STUB_CLASS_MARKERS`.
    """
    if adapter is None:
        return None
    cls = getattr(adapter, "__class__", None)
    if cls is None:
        return None
    class_name = getattr(cls, "__name__", "") or ""
    if not any(marker in class_name for marker in STUB_CLASS_MARKERS):
        return None
    return (slot_name, class_name)


def validate_runtime_profile_adapters(
    *,
    profile: RuntimeProfile,
    adapters: Mapping[str, object],
) -> RuntimeProfileDiagnostic:
    """Validate the wired adapter set against the runtime profile.

    Behaviour by profile:

    - ``TEST``: returns an empty diagnostic. Tests intentionally
      wire stubs and we don't want a per-test warning storm.
    - ``DEVELOPMENT``: returns a diagnostic listing every stub
      adapter so the caller can log it once. Does not raise.
    - ``PRODUCTION``: raises :class:`ConfigurationError` if any
      slot resolves to a stub-class-named adapter. Otherwise
      returns an empty diagnostic. The error message lists every
      offending slot by name so operators can fix the wiring.

    Adapter slots whose value is ``None`` are ignored — the
    production check for ``None`` is enforced separately (the
    orchestrator's own init rejects ``ci_monitor is None`` when
    ``ci_monitor_required=True``; this validator only knows about
    class-name stub markers).
    """
    stubs: list[tuple[str, str]] = []
    for slot_name, adapter in adapters.items():
        classified = _classify_adapter(slot_name, adapter)
        if classified is not None:
            stubs.append(classified)
    if profile == RuntimeProfile.TEST:
        return RuntimeProfileDiagnostic(profile=profile, stub_adapters=())
    if profile == RuntimeProfile.PRODUCTION and stubs:
        offending = ", ".join(f"{name}({cls})" for name, cls in stubs)
        raise ConfigurationError(
            f"runtime_profile=production refuses to start with stub "
            f"adapters wired: {offending}. Wire concrete production "
            f"implementations (e.g. SubprocessGitBackend, "
            f"GitHubPullRequestClient) or set runtime_profile to "
            f"'development' / 'test'."
        )
    return RuntimeProfileDiagnostic(
        profile=profile,
        stub_adapters=tuple(stubs),
    )


def iter_adapter_slots(orchestrator: object) -> Iterable[tuple[str, object]]:
    """Yield (name, value) for each adapter slot on ``orchestrator``.

    Used by the production validator to enumerate the orchestrator's
    wired adapters without coupling the validator to the
    ``Orchestrator`` class itself. The slot list is a stable,
    hard-coded contract: any new adapter slot on ``Orchestrator``
    that should fail-closed in production must be added here.
    """
    slots: tuple[str, ...] = (
        "pr_client",
        "ci_monitor",
        "runner",
        "trace_writer_active",
    )
    for slot in slots:
        yield slot, getattr(orchestrator, slot, None)


__all__ = [
    "RuntimeProfileDiagnostic",
    "STUB_CLASS_MARKERS",
    "iter_adapter_slots",
    "validate_runtime_profile_adapters",
]
