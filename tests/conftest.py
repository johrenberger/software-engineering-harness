"""Top-level pytest conftest: registers project-local testing plugins.

The flaky-test detector plugin lives in
``tests/_testing_helpers/flaky_plugin.py`` (Cluster G G1c). pytest
discovers it via the ``pytest_plugins`` declaration below.
"""

from __future__ import annotations

pytest_plugins = ["tests._testing_helpers.flaky_plugin"]
