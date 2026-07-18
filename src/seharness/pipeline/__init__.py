"""End-to-end vertical slice pipeline.

Implements SPEC §"Phase 8: End-to-end vertical slice" — runs the full
synthesis → completed pipeline on a synthetic fixture repository.
"""

from seharness.pipeline.vertical_slice import (
    PipelineEvent,
    PipelineResult,
    VerticalSlicePipeline,
)

__all__ = ["PipelineEvent", "PipelineResult", "VerticalSlicePipeline"]
