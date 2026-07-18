"""Idempotency keys + file-based JSON store.

Per SPEC §'Slice 9 RED bullet 5':
- Duplicate resume does not create duplicate commits or PRs.
- IdempotencyKey identifies a (run_id, task_id) commit/PR pair.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class IdempotencyKey(BaseModel):
    """Unique identifier for a commit/PR pair in a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)

    def as_filename(self) -> str:
        return f"{self.run_id}__{self.task_id}.json"


class IdempotencyRecord(BaseModel):
    """Persisted record of a commit/PR pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commit_sha: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    pr_url: str | None = None


class IdempotencyStore:
    """File-based JSON store. One file per IdempotencyKey."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def get(self, key: IdempotencyKey) -> IdempotencyRecord | None:
        path = self._root / key.as_filename()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return IdempotencyRecord(**data)

    def put(self, key: IdempotencyKey, record: IdempotencyRecord) -> None:
        path = self._root / key.as_filename()
        path.write_text(
            json.dumps(
                {
                    "commit_sha": record.commit_sha,
                    "branch": record.branch,
                    "pr_url": record.pr_url,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
