"""Readiness validation for production router wiring.

Cluster N (MiniMax M3 refinement) — production-composition step.

The previous ``ModelBackedServiceComposition`` declared
``kind = ProviderKind.LIVE.value`` as a class attribute and let
production startup accept whatever adapter was wired. The
``MiniMaxAdapter`` class itself declared
``kind: ProviderKind = ProviderKind.LIVE`` regardless of whether
the underlying transport was actually functional. This allowed a
non-functional adapter (e.g. the original ``invoke()`` returning
``provider_failure``) to pass as live.

Cluster N replaces class-name substring detection with
**capability-based readiness**: each adapter exposes
``readiness() -> ProviderReadiness`` and ``is_live()`` is the
authoritative answer. The wiring validator in this module walks
the wired router's adapter set and refuses to start in production
when any adapter is not live.

Behaviour by runtime profile (mirrors
``validate_runtime_profile_adapters``):

- ``TEST``: silent no-op. Tests wire fakes deliberately.
- ``DEVELOPMENT``: returns a diagnostic listing every not-live
  adapter so the caller can log a single startup warning. Does
  not raise.
- ``PRODUCTION``: raises :class:`ConfigurationError` if any wired
  adapter is not live. Otherwise returns an empty diagnostic.

The validator is invoked from
``ModelBackedServiceComposition.__init__`` so production startup
fails-closed before any phase runs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from seharness.config import RuntimeProfile
from seharness.domain.enums import ProviderName
from seharness.exceptions import ConfigurationError
from seharness.models.base import ModelAdapter
from seharness.models.provider_readiness import ProviderReadiness


class _RouterLike(Protocol):
    """Minimal surface required by ``validate_router_readiness``.

    The validator only needs to enumerate the wired adapters via
    ``router.adapters``. ``ModelRouter`` exposes this through its
    ``adapters`` property (returning a ``Mapping[ProviderName,
    ModelAdapter]``). Tests can pass a simple dict-wrapper.
    """

    @property
    def adapters(self) -> Mapping[ProviderName, ModelAdapter]: ...


@dataclass(frozen=True)
class ReadinessDiagnostic:
    """Outcome of a readiness validation pass.

    ``not_live_adapters`` lists the (provider, reason) pairs for
    adapters whose ``readiness().is_live()`` returned ``False``.
    Empty for ``PRODUCTION`` (because any not-live adapter raises)
    and for ``TEST`` (we don't bother enumerating).
    """

    profile: RuntimeProfile
    not_live_adapters: tuple[tuple[str, str], ...]


def _readiness_for(adapter: ModelAdapter) -> ProviderReadiness:
    """Return the adapter's readiness, or a synthetic not-live
    readiness if the adapter does not implement the readiness
    contract.

    Adapters that have not been updated for cluster N
    (e.g. the legacy ``FakeModelAdapter`` that does not call the
    live HTTP path) return ``is_live() == False`` here so
    production startup refuses to start.
    """
    readiness_callable = getattr(adapter, "readiness", None)
    if readiness_callable is None:
        return ProviderReadiness(
            configured=False,
            transport_available=False,
            transport_is_live=False,
            model_identifier="unset",
            reason="adapter does not implement readiness()",
        )
    try:
        readiness = readiness_callable()
    except Exception as exc:
        return ProviderReadiness(
            configured=False,
            transport_available=False,
            transport_is_live=False,
            model_identifier="unset",
            reason=f"readiness probe raised: {exc!s}",
        )
    if not isinstance(readiness, ProviderReadiness):
        return ProviderReadiness(
            configured=False,
            transport_available=False,
            transport_is_live=False,
            model_identifier="unset",
            reason="readiness() did not return a ProviderReadiness",
        )
    return readiness


def validate_router_readiness(
    *,
    profile: RuntimeProfile,
    router: _RouterLike,
) -> ReadinessDiagnostic:
    """Validate that every wired adapter is live for ``profile``.

    Parameters
    ----------
    profile
        The runtime profile. ``PRODUCTION`` raises when any
        adapter is not live; ``DEVELOPMENT`` returns a diagnostic;
        ``TEST`` silently passes.
    router
        The router-like object exposing ``adapters`` as a mapping
        of provider name → adapter instance.

    Returns
    -------
    ReadinessDiagnostic
        Empty for ``PRODUCTION`` (raise path) and ``TEST``;
        populated for ``DEVELOPMENT`` so the caller can log a
        single startup warning.

    Raises
    ------
    ConfigurationError
        When ``profile == PRODUCTION`` and any adapter's
        ``readiness().is_live()`` returns ``False``.
    """
    not_live: list[tuple[str, str]] = []
    for provider_name, adapter in router.adapters.items():
        readiness = _readiness_for(adapter)
        if not readiness.is_live():
            not_live.append(
                (
                    str(provider_name),
                    readiness.reason or "readiness.is_live() returned False",
                )
            )

    if profile == RuntimeProfile.TEST:
        return ReadinessDiagnostic(profile=profile, not_live_adapters=())
    if profile == RuntimeProfile.PRODUCTION and not_live:
        offending = ", ".join(f"{name}({reason})" for name, reason in not_live)
        raise ConfigurationError(
            f"runtime_profile=production refuses to start: the "
            f"following wired adapters are not live: {offending}. "
            f"Wire a production adapter (e.g. MiniMaxAdapter with "
            f"HttpMiniMaxTransport + valid MINIMAX_API_KEY + "
            f"MINIMAX_MODEL listed in /v1/models) or set "
            f"runtime_profile to 'development' / 'test'."
        )
    return ReadinessDiagnostic(
        profile=profile,
        not_live_adapters=tuple(not_live),
    )


def iter_not_live_adapters(
    router: _RouterLike,
) -> Iterable[tuple[str, ProviderReadiness]]:
    """Yield (provider, readiness) pairs for every not-live adapter.

    Convenience helper for the orchestrator's startup log line.
    """
    for provider_name, adapter in router.adapters.items():
        readiness = _readiness_for(adapter)
        if not readiness.is_live():
            yield str(provider_name), readiness


__all__ = [
    "ReadinessDiagnostic",
    "iter_not_live_adapters",
    "validate_router_readiness",
]
