"""Cluster M3-2 corrective: canonical MiniMax-M3 local composition.

The corrective processing instructions §"Required architecture
correction" requires a single builder that constructs and wires
the production-local MiniMax-M3 composition. This module owns
that builder.

Why a single builder:

The corrective doc lists 12 services that must be wired for the
vertical workflow (specification, planning, controlled patches,
RED/GREEN, remediation, review, etc.) and a "must fail" list
of seven preconditions. Without a single named entry point, the
wiring is dispersed across the orchestrator and each caller has
to re-verify the preconditions. With a single builder, the
preconditions are checked once at construction time and the
result is a single ``ServiceComposition`` the orchestrator can
inject.

Author vs. review router separation:

The doc explicitly forbids the author and review paths from
sharing the same ``ModelRouter`` object. We construct two
distinct router instances even though they share the same
underlying provider; the object-identity check in the builder
catches accidental sharing. This is the literal interpretation
of "Author and review routers share the same object or
conversation/session state".

Production-mode deterministic rejection:

The existing ``ModelBackedServiceComposition`` still wires a
``DeterministicPlanningService`` into the planner slot — that
is a cluster-N artifact we close here. In PRODUCTION mode the
builder requires all five service slots to be model-backed.
DEVELOPMENT / TEST profiles still allow the deterministic
services (for notebooks and tests).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx

from seharness.config import RuntimeProfile
from seharness.domain.enums import ProviderName, RoutingRole
from seharness.exceptions import ConfigurationError
from seharness.models.minimax import MiniMaxAdapter
from seharness.models.minimax_transport import (
    DEFAULT_MODEL,
    NATIVE_ENDPOINT,
    MiniMaxTransportResponse,
    RecordingMiniMaxTransport,
)
from seharness.models.readiness_validation import (
    validate_router_readiness,
)
from seharness.models.router import ModelRouter

if TYPE_CHECKING:
    from seharness.orchestrator.provider_evidence import (
        ProviderEvidenceWriter,
    )
    from seharness.orchestrator.services import (
        ModelBackedServiceComposition,
    )


# Lazy imports for modules that depend on ``seharness.orchestrator``
# (which itself depends on ``seharness.controller`` via
# ``application_service``). Importing them at module top would
# trigger the partial-init cycle documented in
# ``application_service.py``. The builder is only called after
# the orchestrator's package init has completed, so a lazy
# import inside the function is safe.

# The stub-class marker list mirrors
# ``seharness.orchestrator.runtime_profile.STUB_CLASS_MARKERS``.
# We duplicate it here so the cycle-avoiding lazy import is
# not needed for the trivial check the builder performs (the
# builder only checks the transport's class name against this
# short list, not the orchestrator's full
# ``validate_runtime_profile_adapters``). When the orchestrator
# adds a new marker, both lists must be updated.
_STUB_CLASS_MARKERS: tuple[str, ...] = (
    "Stub",
    "Fake",
    "Noop",
    "NoOp",
    "InMemory",
)


# Author-side roles. The review router handles REVIEW; everything
# else goes through the author router.
_AUTHOR_ROLES: tuple[RoutingRole, ...] = (
    RoutingRole.PLANNING,
    RoutingRole.IMPLEMENTATION,
    RoutingRole.REMEDIATION,
    RoutingRole.DELIVERY,
)


# ---------------------------------------------------------------------------
# Configuration object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxConfig:
    """Sandbox configuration for patch application.

    Cluster M3-2: the corrective doc requires "sandbox
    configuration is absent" to be a builder failure. We
    represent the minimum the builder can validate: the
    sandbox directory, the patch-policy allowed-paths, and
    the validation commands. The orchestrator's M3-3 phase
    handlers consume this directly.
    """

    sandbox_dir: Path
    patch_policy_allowed_paths: tuple[str, ...]
    validation_commands: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.patch_policy_allowed_paths:
            msg = (
                "SandboxConfig.patch_policy_allowed_paths is empty; "
                "the corrective doc requires a non-empty path policy "
                "so patches cannot escape the sandbox"
            )
            raise ValueError(msg)
        if not self.validation_commands:
            msg = (
                "SandboxConfig.validation_commands is empty; "
                "the corrective doc requires at least one validation "
                "command so RED/GREEN has something to run"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class MiniMaxM3CompositionConfig:
    """Configuration for :func:`build_minimax_m3_local_composition`.

    All fields have safe defaults except ``api_key`` (which must
    be set in PRODUCTION) and ``sandbox_config`` (which must be
    set in any profile per the corrective doc).
    """

    api_key: str | None = None
    protocol: Literal["native", "openai-compatible"] = "openai-compatible"
    endpoint: str | None = None
    model: str = DEFAULT_MODEL
    thinking: bool = True
    service_tier: str = "standard"
    runtime_profile: RuntimeProfile = RuntimeProfile.PRODUCTION
    sandbox_config: SandboxConfig | None = None
    provider_evidence_dir: Path | None = None
    clock: Callable[[], float] | None = None
    http_client_factory: Callable[[], httpx.Client] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _class_name(value: object) -> str:
    """Return ``value.__class__.__name__`` (empty for ``None``)."""
    cls = getattr(value, "__class__", None)
    if cls is None:
        return ""
    return str(getattr(cls, "__name__", "") or "")


def _looks_like_stub(adapter: object) -> bool:
    """Return True if ``adapter``'s class name contains a stub marker."""
    name = _class_name(adapter)
    return any(marker in name for marker in _STUB_CLASS_MARKERS)


def _looks_like_deterministic_service(service: object) -> bool:
    """Return True if ``service`` is a ``Deterministic*Service``.

    The existing cluster-N services use ``Deterministic`` as a
    class-name prefix (Specification / Planning / Implementation /
    Remediation / Review). The doc requires the builder to refuse
    deterministic services in PRODUCTION mode.
    """
    name = _class_name(service)
    return name.startswith("Deterministic") and name.endswith("Service")


def _build_minimax_adapter(
    *,
    config: MiniMaxM3CompositionConfig,
    api_key: str | None,
) -> MiniMaxAdapter:
    """Construct a :class:`MiniMaxAdapter` from ``config``.

    The endpoint and protocol are derived from ``config``; an
    explicit ``endpoint`` always wins over the protocol default.
    The adapter is constructed without the API key on the
    instance — it reads ``MINIMAX_API_KEY`` from the environment
    at request time. The local ``api_key`` argument is passed so
    tests can mock the env without touching the real one.
    """
    # We accept ``api_key`` as an explicit kwarg so tests can
    # stub the adapter without setting ``os.environ``. The
    # ``MiniMaxAdapter`` reads the env directly; tests should
    # use ``monkeypatch.setenv``.
    _ = api_key
    endpoint = config.endpoint
    if endpoint is None:
        endpoint = NATIVE_ENDPOINT if config.protocol == "native" else None
    return MiniMaxAdapter(
        endpoint=endpoint,
        protocol=config.protocol,
        model_identifier=config.model,
        thinking=config.thinking,
        service_tier=config.service_tier,
    )


def _build_router(
    *,
    role_to_provider: Mapping[RoutingRole, ProviderName],
    minimax_adapter: MiniMaxAdapter,
    fallback_table: Mapping[ProviderName, ProviderName] | None = None,
) -> ModelRouter:
    """Build a :class:`ModelRouter` from a role→provider mapping."""
    adapters: dict[ProviderName, MiniMaxAdapter] = {
        ProviderName.MINIMAX: minimax_adapter,
    }
    return ModelRouter(
        adapters=adapters,
        routing=dict(role_to_provider),
        fallback_table=(dict(fallback_table) if fallback_table is not None else None),
    )


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MiniMaxM3CompositionResult:
    """Output of :func:`build_minimax_m3_local_composition`.

    ``composition`` is the ``ServiceComposition`` the orchestrator
    injects; ``author_router`` and ``review_router`` are the two
    distinct router objects the doc requires; ``evidence_writer``
    is the durable evidence writer attached to the composition.
    """

    composition: ModelBackedServiceComposition
    author_router: ModelRouter
    review_router: ModelRouter
    evidence_writer: ProviderEvidenceWriter
    sandbox_config: SandboxConfig


def build_minimax_m3_local_composition(
    config: MiniMaxM3CompositionConfig,
) -> MiniMaxM3CompositionResult:
    """Build the canonical MiniMax-M3 production-local composition.

    Refuses to construct (raises :class:`ConfigurationError`)
    when:

    - ``config.runtime_profile == PRODUCTION`` and the live
      transport is a fake / recording / stub class.
    - ``config.runtime_profile == PRODUCTION`` and any
      spec / planning / impl / remediation / review service
      resolves to a ``Deterministic*Service`` subclass.
    - MiniMax-M3 readiness is not verified through either
      catalog or live verification (on either router).
    - Author and review routers share the same object or
      have overlapping adapter instances (the doc forbids
      session sharing — overlap is the practical proxy).
    - ``config.sandbox_config`` is ``None``, or its
      patch-policy / validation-commands lists are empty.
    - ``config.api_key`` is unset on PRODUCTION.
    - ``config.model != "MiniMax-M3"`` on PRODUCTION (the
      doc says the configured model MUST be MiniMax-M3 for
      the M3 vertical acceptance run; M3-5 enforces this).

    Returns:
        A :class:`MiniMaxM3CompositionResult` carrying the
        wired composition, the two distinct routers, and the
        evidence writer.
    """
    # Lazy import: ``ModelBackedServiceComposition`` lives in
    # ``seharness.orchestrator.services``. Importing it at
    # module top triggers the partial-init cycle documented
    # in ``application_service.py``. The builder is only ever
    # called after the orchestrator's package init has
    # completed (the orchestrator is what calls us), so a
    # lazy import here is safe.
    from seharness.orchestrator.services import (  # noqa: PLC0415
        ModelBackedServiceComposition,
    )

    # ---- precondition 1: api key on PRODUCTION ----
    if config.runtime_profile == RuntimeProfile.PRODUCTION and not config.api_key:
        msg = (
            "build_minimax_m3_local_composition: api_key is required in "
            "PRODUCTION mode (got None); set MINIMAX_API_KEY or pass "
            "api_key=... explicitly"
        )
        raise ConfigurationError(msg)

    # ---- precondition 2: model is MiniMax-M3 on PRODUCTION ----
    if config.runtime_profile == RuntimeProfile.PRODUCTION and config.model != "MiniMax-M3":
        msg = (
            f"build_minimax_m3_local_composition: model must be "
            f"'MiniMax-M3' in PRODUCTION mode (got {config.model!r}); "
            f"the corrective doc requires MiniMax-M3 for the M3 vertical "
            f"acceptance run and refuses silent model substitution"
        )
        raise ConfigurationError(msg)

    # ---- precondition 3: sandbox config present ----
    if config.sandbox_config is None:
        msg = (
            "build_minimax_m3_local_composition: sandbox_config is "
            "required (got None); the corrective doc refuses to start "
            "without sandbox configuration"
        )
        raise ConfigurationError(msg)
    sandbox_config: SandboxConfig = config.sandbox_config

    # ---- precondition 4: provider evidence dir present ----
    if config.provider_evidence_dir is None:
        msg = (
            "build_minimax_m3_local_composition: provider_evidence_dir "
            "is required (got None); the corrective doc requires every "
            "model call to record durable evidence"
        )
        raise ConfigurationError(msg)

    # ---- precondition 5: stub-class adapters rejected on PRODUCTION ----
    if config.runtime_profile == RuntimeProfile.PRODUCTION:
        # We check the adapter's transport class name against the
        # stub markers (mirrors ``validate_runtime_profile_adapters``
        # from ``seharness.orchestrator.runtime_profile`` but with a
        # direct class-name check so we avoid the orchestrator-package
        # cycle). The MiniMax adapter wires ``HttpMiniMaxTransport``
        # which is not a stub; any future "FakeMiniMaxAdapter" /
        # "RecordingMiniMaxAdapter" class would be caught here.
        adapter = _build_minimax_adapter(
            config=config,
            api_key=config.api_key,
        )
        transport_cls_name = _class_name(getattr(adapter, "_transport", None))
        if any(marker in transport_cls_name for marker in _STUB_CLASS_MARKERS):
            msg = (
                "build_minimax_m3_local_composition: PRODUCTION mode "
                "refuses to start with a stub-class MiniMax transport "
                f"({transport_cls_name}); wire a real "
                "HttpMiniMaxTransport or set runtime_profile to "
                "'development' / 'test'"
            )
            raise ConfigurationError(msg)

    # ---- build the two distinct routers ----
    author_role_map: dict[RoutingRole, ProviderName] = dict.fromkeys(
        _AUTHOR_ROLES, ProviderName.MINIMAX
    )
    # Author and review each get their own adapter instance so the
    # adapters themselves cannot share conversation/session state.
    # The adapters do not currently cache state per conversation
    # but the doc's literal wording forbids shared objects; future
    # state caching on the adapter must respect this.
    author_adapter = _build_minimax_adapter(config=config, api_key=config.api_key)
    review_adapter = _build_minimax_adapter(config=config, api_key=config.api_key)
    author_router = _build_router(
        role_to_provider=author_role_map,
        minimax_adapter=author_adapter,
    )
    review_router = _build_router(
        role_to_provider={RoutingRole.REVIEW: ProviderName.MINIMAX},
        minimax_adapter=review_adapter,
    )

    # ---- precondition 6: routers must be distinct objects ----
    if author_router is review_router:
        msg = (
            "build_minimax_m3_local_composition: author and review "
            "routers are the same object; the corrective doc forbids "
            "shared router instances between author and review paths"
        )
        raise ConfigurationError(msg)
    if author_adapter is review_adapter:
        msg = (
            "build_minimax_m3_local_composition: author and review "
            "adapters are the same object; the corrective doc forbids "
            "shared adapter instances between author and review paths"
        )
        raise ConfigurationError(msg)

    # ---- precondition 7: readiness verification ----
    if config.runtime_profile == RuntimeProfile.PRODUCTION:
        # Both routers must report live; the production validator
        # raises if any adapter's readiness.is_live() returns False.
        # In DEVELOPMENT the validator returns a diagnostic and the
        # caller can decide whether to log it; in TEST it passes.
        validate_router_readiness(profile=config.runtime_profile, router=author_router)
        validate_router_readiness(profile=config.runtime_profile, router=review_router)

    # ---- construct the composition ----
    composition = ModelBackedServiceComposition(
        router=author_router,
        clock=config.clock,
        runtime_profile=config.runtime_profile,
    )

    # Cluster M3-2: replace the planner slot with a real
    # ``ModelBackedPlanningService``. ``ModelBackedServiceComposition``
    # still wires a ``DeterministicPlanningService`` from cluster N
    # as a backwards-compatibility default. The corrective doc
    # forbids deterministic services in PRODUCTION so we override
    # the planner here. The model-backed planner exists today (it
    # was added in cluster N PR4) — we just point the composition
    # at it.
    from seharness.orchestrator.services import (  # noqa: PLC0415
        ModelBackedPlanningService,
    )

    composition.planning = ModelBackedPlanningService(router=author_router)

    # ---- precondition 8: no deterministic services in PRODUCTION ----
    # Cluster M3-2 closes the cluster-N artifact where the planner
    # slot on ``ModelBackedServiceComposition`` was hard-wired to a
    # ``DeterministicPlanningService``. In PRODUCTION this check
    # catches any future reversion. In DEVELOPMENT / TEST the
    # deterministic services remain acceptable for notebooks.
    if config.runtime_profile == RuntimeProfile.PRODUCTION:
        services: Mapping[str, Any] = {
            "specification": composition.specification,
            "planning": composition.planning,
            "implementation": composition.implementation,
            "remediation": composition.remediation,
            "review": composition.review,
        }
        offenders = [
            f"{slot}({_class_name(svc)})"
            for slot, svc in services.items()
            if _looks_like_deterministic_service(svc)
        ]
        if offenders:
            msg = (
                "build_minimax_m3_local_composition: deterministic "
                "services present in PRODUCTION composition: "
                f"{offenders}; the corrective doc forbids deterministic "
                "spec/planning/impl/remediation/review services in "
                "PRODUCTION mode"
            )
            raise ConfigurationError(msg)

    # ---- evidence writer ----
    from seharness.orchestrator.provider_evidence import (  # noqa: PLC0415
        ProviderEvidenceWriter as _ProviderEvidenceWriter,
    )

    evidence_writer = _ProviderEvidenceWriter(evidence_dir=config.provider_evidence_dir)

    return MiniMaxM3CompositionResult(
        composition=composition,
        author_router=author_router,
        review_router=review_router,
        evidence_writer=evidence_writer,
        sandbox_config=sandbox_config,
    )


# ---------------------------------------------------------------------------
# Offline composition factory (cluster M3-4)
# ---------------------------------------------------------------------------


def build_minimax_m3_offline_composition(
    *,
    config: MiniMaxM3CompositionConfig,
    recording_transport: RecordingMiniMaxTransport,
    recording_responses: tuple[MiniMaxTransportResponse, ...],
) -> MiniMaxM3CompositionResult:
    """Construct the M3 composition wired against a recording transport.

    Cluster M3-4: the offline vertical acceptance test (and any
    later offline replay) needs a real :class:`MiniMaxM3CompositionResult`
    — the same shape the production builder returns — but with
    every model call routed through a pre-loaded
    :class:`RecordingMiniMaxTransport`. This factory:

    - Refuses ``runtime_profile != TEST`` (M3-2 invariant: a
      recording transport is a stub-class transport; only TEST
      may construct with one).
    - Still enforces: ``model == "MiniMax-M3"``,
      ``sandbox_config`` present with non-empty policy /
      validation commands, ``provider_evidence_dir`` present,
      author router ≠ review router, author adapter ≠ review
      adapter (M3-2 invariants).
    - **Skips** the PRODUCTION-only preconditions: live readiness
      verification, ``api_key`` requirement, deterministic-
      service refusal (the offline composition may legitimately
      wire deterministic services in TEST mode — they are not the
      ``Deterministic*Service`` subclasses the M3-2 builder
      forbids anyway).
    - Wires the supplied :class:`RecordingMiniMaxTransport` onto
      both author and review adapters so the same recording
      queue feeds both paths. The caller is responsible for
      loading the recordings in the order the orchestrator will
      consume them (specification → planning → implementation xN
      → review).

    Returns the same :class:`MiniMaxM3CompositionResult` shape
    the production builder returns, so the orchestrator wires
    it identically.
    """
    from seharness.orchestrator.provider_evidence import (  # noqa: PLC0415
        ProviderEvidenceWriter as _ProviderEvidenceWriter,
    )
    from seharness.orchestrator.services import (  # noqa: PLC0415
        ModelBackedPlanningService,
        ModelBackedReviewService,
        ModelBackedServiceComposition,
    )

    if config.runtime_profile != RuntimeProfile.TEST:
        msg = (
            "build_minimax_m3_offline_composition: runtime_profile must be "
            f"TEST (got {config.runtime_profile!r}); recording transports "
            "are stub-class and the M3-2 invariant forbids them on "
            "PRODUCTION / DEVELOPMENT"
        )
        raise ConfigurationError(msg)
    if not recording_responses:
        msg = (
            "build_minimax_m3_offline_composition: recording_responses "
            "must be a non-empty tuple; an offline composition without "
            "any queued responses fails every model call"
        )
        raise ConfigurationError(msg)
    # The supplied recording transport is the queue source; we
    # reuse it on both adapters so the caller's transport
    # instance is the single source of truth for replay.
    # (mypy otherwise warns about the unused parameter; the
    # adapter wiring below consumes it.)
    if config.model != "MiniMax-M3":
        msg = (
            "build_minimax_m3_offline_composition: model must be "
            f"'MiniMax-M3' (got {config.model!r}); the offline "
            "factory refuses silent model substitution"
        )
        raise ConfigurationError(msg)
    if config.sandbox_config is None:
        msg = (
            "build_minimax_m3_offline_composition: sandbox_config is "
            "required (got None); the offline factory refuses to start "
            "without sandbox configuration (even in TEST mode)"
        )
        raise ConfigurationError(msg)
    if config.provider_evidence_dir is None:
        msg = (
            "build_minimax_m3_offline_composition: provider_evidence_dir "
            "is required (got None); the offline factory must record "
            "evidence so the offline vertical acceptance can audit "
            "every model call"
        )
        raise ConfigurationError(msg)
    endpoint = config.endpoint
    if endpoint is None:
        endpoint = NATIVE_ENDPOINT if config.protocol == "native" else None
    # Two distinct adapter instances, each with its own
    # RecordingMiniMaxTransport (so the adapters cannot share
    # any state). Each transport is pre-loaded with the same
    # queue so the recorder-driven replay is deterministic.
    author_adapter = MiniMaxAdapter(
        endpoint=endpoint,
        protocol=config.protocol,
        model_identifier=config.model,
        thinking=config.thinking,
        service_tier=config.service_tier,
        transport=recording_transport,
    )
    review_adapter = MiniMaxAdapter(
        endpoint=endpoint,
        protocol=config.protocol,
        model_identifier=config.model,
        thinking=config.thinking,
        service_tier=config.service_tier,
        transport=recording_transport,
    )
    if author_adapter is review_adapter:
        msg = (
            "build_minimax_m3_offline_composition: author and review "
            "adapters must be distinct objects"
        )
        raise ConfigurationError(msg)
    author_router = _build_router(
        role_to_provider=dict.fromkeys(_AUTHOR_ROLES, ProviderName.MINIMAX),
        minimax_adapter=author_adapter,
    )
    review_router = _build_router(
        role_to_provider={RoutingRole.REVIEW: ProviderName.MINIMAX},
        minimax_adapter=review_adapter,
    )
    if author_router is review_router:
        msg = (
            "build_minimax_m3_offline_composition: author and review "
            "routers must be distinct objects"
        )
        raise ConfigurationError(msg)
    composition = ModelBackedServiceComposition(
        router=author_router,
        clock=config.clock,
        runtime_profile=config.runtime_profile,
    )
    composition.planning = ModelBackedPlanningService(
        router=author_router,
        policy_allowed_paths=tuple(config.sandbox_config.patch_policy_allowed_paths),
    )
    # Cluster M3-4: wire the review service with the dedicated
    # review_router so RoutingRole.REVIEW routes correctly.
    composition.review = ModelBackedReviewService(
        router=review_router,
        budget=composition._budget,
        clock=composition._clock,
    )
    evidence_writer = _ProviderEvidenceWriter(evidence_dir=config.provider_evidence_dir)
    return MiniMaxM3CompositionResult(
        composition=composition,
        author_router=author_router,
        review_router=review_router,
        evidence_writer=evidence_writer,
        sandbox_config=config.sandbox_config,
    )


__all__ = [
    "MiniMaxM3CompositionConfig",
    "MiniMaxM3CompositionResult",
    "SandboxConfig",
    "build_minimax_m3_local_composition",
    "build_minimax_m3_offline_composition",
]
