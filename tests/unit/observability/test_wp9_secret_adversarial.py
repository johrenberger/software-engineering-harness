"""WP9 (Cluster N) — secret-leakage and environment adversarial tests.

The handoff lists "Secret files and environment leakage" and
"Network exfiltration" as adversarial cases. The harness has
two enforcement boundaries:

1. :class:`SecretRedactor` scrubs known secret patterns from
   trace events before they are written to disk or emitted
   on the wire.
2. :class:`SandboxProfile` blocks network egress by default
   (allow-list of allowed domains) and denies canonical
   secret env vars (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
   ``GITHUB_TOKEN``, ``AWS_*``, etc.).

These tests pin the ENFORCED BOUNDARY for each case and the
EXPECTED FAILURE STATE.
"""

from __future__ import annotations

import pytest

from seharness.observability.redactor import (
    REDACTION_SENTINEL,
    SecretRedactor,
)

# ---------------------------------------------------------------------------
# WP9.14 — Secret redactor
# ---------------------------------------------------------------------------


class TestSecretRedactorBasic:
    """ENFORCED BOUNDARY: every known secret pattern is
    replaced with the sentinel. EXPECTED FAILURE STATE: the
    input text no longer contains the secret substring."""

    def test_empty_string_passes_through(self) -> None:
        assert SecretRedactor().redact("") == ""

    def test_no_secrets_unchanged(self) -> None:
        text = "this is a normal commit message about fixing a bug"
        assert SecretRedactor().redact(text) == text

    @pytest.mark.parametrize(
        "secret",
        [
            # Telegram bot token
            "1234567890:AABBccDDeeFFggHHiiJJkkLLmmNNooPPqq",
            # OpenAI project key
            "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
            # OpenAI classic key
            "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            # Anthropic key
            "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            # GitHub PAT
            "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            # GitHub server token
            "ghs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            # AWS access key ID
            "AKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_known_patterns_redacted(self, secret: str) -> None:
        out = SecretRedactor().redact(f"prefix {secret} suffix")
        assert secret not in out
        assert REDACTION_SENTINEL in out


class TestSecretRedactorAssignments:
    """ENFORCED BOUNDARY: ``password=...``, ``token=...``,
    ``api_key=...``, ``apikey=...``, ``secret=...`` key=value
    pairs in arbitrary text are redacted. EXPECTED FAILURE STATE:
    the value is replaced; the key may remain (or also be
    replaced — pinned separately)."""

    @pytest.mark.parametrize(
        "text,leak",
        [
            ("password=hello123", "hello123"),
            ("password = 'hello123'", "hello123"),
            ('password = "hello123"', "hello123"),
            ("PASSWORD=hello123", "hello123"),
            ("token=abc123def456", "abc123def456"),
            ("api_key=my-secret-value", "my-secret-value"),
            ("apikey=my-secret-value", "my-secret-value"),
            ("secret=very-secret", "very-secret"),
        ],
    )
    def test_key_value_pair_redacted(self, text: str, leak: str) -> None:
        out = SecretRedactor().redact(text)
        assert leak not in out
        assert REDACTION_SENTINEL in out


class TestSecretRedactorAuthHeaders:
    """ENFORCED BOUNDARY: HTTP ``Authorization: Basic ...``
    and ``Authorization: Bearer ...`` headers are redacted."""

    def test_basic_auth_redacted(self) -> None:
        out = SecretRedactor().redact("Authorization: Basic dXNlcjpwYXNz")
        assert "dXNlcjpwYXNz" not in out

    def test_bearer_token_redacted(self) -> None:
        out = SecretRedactor().redact("Authorization: Bearer ya29.a0AfH6SMBxxxxxxxxxxxxxxxx")
        # The long token portion must be gone; the literal
        # "Bearer" keyword MAY remain (not a secret).
        assert "ya29.a0AfH6SMB" not in out

    def test_case_insensitive_authorization(self) -> None:
        out = SecretRedactor().redact("AUTHORIZATION: BASIC dXNlcjpwYXNz")
        assert "dXNlcjpwYXNz" not in out


class TestSecretRedactorDict:
    """ENFORCED BOUNDARY: ``redact_dict`` walks dicts
    recursively so structured payloads (trace events,
    JSON output) are scrubbed before serialisation."""

    def test_flat_dict_redacted(self) -> None:
        d = {
            "user": "alice",
            "api_key": "AKIAIOSFODNN7EXAMPLE",
        }
        out = SecretRedactor().redact_dict(d)
        assert out["user"] == "alice"
        assert "AKIAIOSFODNN7EXAMPLE" not in out["api_key"]

    def test_nested_dict_redacted(self) -> None:
        d = {
            "outer": {
                "inner": "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
        }
        out = SecretRedactor().redact_dict(d)
        assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in out["outer"]["inner"]

    def test_list_of_strings_redacted(self) -> None:
        d = {
            "tokens": [
                "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "safe text",
            ],
        }
        out = SecretRedactor().redact_dict(d)
        assert REDACTION_SENTINEL in out["tokens"][0]
        assert out["tokens"][1] == "safe text"

    def test_non_string_values_preserved(self) -> None:
        d = {
            "count": 42,
            "ratio": 0.5,
            "ok": True,
            "none": None,
        }
        out = SecretRedactor().redact_dict(d)
        assert out == d

    def test_dict_input_not_mutated(self) -> None:
        d = {"api_key": "AKIAIOSFODNN7EXAMPLE"}
        SecretRedactor().redact_dict(d)
        # The original dict is untouched.
        assert d["api_key"] == "AKIAIOSFODNN7EXAMPLE"


# ---------------------------------------------------------------------------
# WP9.15 — Environment variable leakage
# ---------------------------------------------------------------------------


class TestEnvironmentVariableScanning:
    """ENFORCED BOUNDARY: the redactor also catches secrets
    formatted as ``$ENV_VAR`` references when the secret
    VALUE itself is in the text. (The harness does NOT
    read process environment into redacted text — only
    literal values are scrubbed.) EXPECTED FAILURE STATE:
    literal values of secret-looking patterns are redacted;
    env-var references are left as-is (not a secret)."""

    def test_literal_token_in_env_value(self) -> None:
        # If a process exports ``GITHUB_TOKEN=ghp_...``, the
        # literal token value will be in the env. If that
        # leaks into a log line, it must be redacted.
        text = "GITHUB_TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        out = SecretRedactor().redact(text)
        assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in out

    def test_env_ref_to_safe_var_not_redacted(self) -> None:
        # ``$HOME`` references are NOT secrets — they're
        # placeholder syntax. The redactor does not
        # treat them as secrets.
        out = SecretRedactor().redact("log_dir=$HOME/logs")
        # The reference is left alone.
        assert "$HOME" in out


class TestEnvDenyList:
    """ENFORCED BOUNDARY: the sandbox denies canonical
    secret env vars so a subprocess can't read them
    even if the parent process has them set.

    EXPECTED FAILURE STATE: ``SandboxProfile.denied_env_vars``
    contains the canonical secret env-var names by default."""

    def test_sandbox_denies_openai_key_by_default(self) -> None:
        from seharness.sandbox.profile import SandboxProfile

        profile = SandboxProfile()
        assert "OPENAI_API_KEY" in profile.denied_env_vars

    def test_sandbox_denies_anthropic_key_by_default(self) -> None:
        from seharness.sandbox.profile import SandboxProfile

        profile = SandboxProfile()
        assert "ANTHROPIC_API_KEY" in profile.denied_env_vars

    def test_sandbox_denies_github_token_by_default(self) -> None:
        from seharness.sandbox.profile import SandboxProfile

        profile = SandboxProfile()
        assert "GITHUB_TOKEN" in profile.denied_env_vars

    def test_sandbox_denies_aws_keys_by_default(self) -> None:
        from seharness.sandbox.profile import SandboxProfile

        profile = SandboxProfile()
        assert "AWS_ACCESS_KEY_ID" in profile.denied_env_vars
        assert "AWS_SECRET_ACCESS_KEY" in profile.denied_env_vars

    def test_sandbox_denies_path_home_user(self) -> None:
        # The deny list also includes shell-tainting env
        # vars (PATH, HOME, USER) so a subprocess can't
        # accidentally inherit the host's shell context.
        from seharness.sandbox.profile import SandboxProfile

        profile = SandboxProfile()
        for var in ("PATH", "HOME", "USER", "SHELL"):
            assert var in profile.denied_env_vars

    def test_user_cannot_remove_default_deny_entries(self) -> None:
        # The deny list is MERGED with defaults; the user
        # can ADD entries but cannot remove them. This
        # prevents accidental secret leakage by a
        # permissive caller.
        from seharness.sandbox.profile import SandboxProfile

        # The defaults are preserved even when the user
        # supplies a single-entry deny list.
        profile = SandboxProfile(denied_env_vars=("MY_VAR",))  # type: ignore[arg-type]
        assert "OPENAI_API_KEY" in profile.denied_env_vars
        assert "MY_VAR" in profile.denied_env_vars
