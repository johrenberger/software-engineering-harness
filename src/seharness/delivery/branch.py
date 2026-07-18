"""Branch service. Per SPEC §'19. Git Automation'.

Branch format is parameterized (B1 decision):
- Production: 'ai/feature/<feature-id>-<slug>'
- Slice-9 tests: 'agent/<slice-number>-<slug>'
"""

from __future__ import annotations

from pathlib import Path
from string import Formatter

from pydantic import BaseModel, ConfigDict, Field

from seharness.delivery.backend import GitBackend


class BranchFormat(BaseModel):
    """Branch name template. Uses Python str.format placeholders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template: str = Field(
        default="ai/feature/{feature_id}-{slug}",
        min_length=1,
    )

    def render(self, **kwargs: str) -> str:
        # Validate that all required placeholders are provided.
        placeholders = {fname for _, fname, _, _ in Formatter().parse(self.template) if fname}
        missing = placeholders - set(kwargs)
        if missing:
            raise KeyError(f"missing placeholder values for branch format: {sorted(missing)}")
        return self.template.format(**kwargs)


class BranchService:
    """Creates branches using the configured format + backend."""

    def __init__(
        self,
        *,
        backend: GitBackend,
        branch_format: BranchFormat | None = None,
    ) -> None:
        self._backend = backend
        self._format = branch_format or BranchFormat()

    @property
    def format(self) -> BranchFormat:
        return self._format

    def create(self, repo_root: Path, **placeholders: str) -> str:
        name = self._format.render(**placeholders)
        self._backend.create_branch(repo_root, name)
        return name
