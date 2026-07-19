# Security Policy

## Reporting a vulnerability

**Please do NOT file a public GitHub issue for security vulnerabilities.**

The software-engineering-harness maintainers take all reports seriously
and will investigate credible reports promptly. We follow
[coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure):
give us a reasonable window to investigate and patch before public
disclosure.

### Preferred: GitHub Security Advisories

Use [GitHub Security Advisories](https://github.com/johrenberger/software-engineering-harness/security/advisories/new)
to report privately. This is the recommended channel because it:

* keeps the report out of public issues,
* lets us discuss details, patches, and disclosure timing in a private
  thread,
* makes it easy to publish a [GHSA advisory](https://docs.github.com/en/code-security/security-advisories)
  with a CVE request at the same time as the fix lands.

### Alternative: Email

If you cannot use GitHub Security Advisories, email
`security@openclaw.eu` (or open an issue tagged `security` if no
sensitive PoC, and we will move it private).

### What to include

A useful report has:

1. **Title** &mdash; short description of the vulnerability.
2. **Affected component(s)** &mdash; module path, function name, CLI
   command, or workflow file. With this project: most reports are
   about `src/seharness/sandbox/`, `src/seharness/orchestrator/`,
   or `.github/workflows/`.
3. **Reproduction steps** &mdash; minimal script, container, or
   `uv run` command. Include the `seharness` version + commit SHA.
4. **Impact** &mdash; what an attacker gains (RCE, info disclosure,
   auth bypass, supply-chain MITM, etc.). Sandboxes, orchestrators,
   and CI workflows are higher-impact than docs changes.
5. **Optional** &mdash; PoC patch (greatly speeds up triage).

Reports in English are preferred; we'll work with other languages
on a best-effort basis.

## Response timeline

We aim for:

| Stage | Target | Notes |
|---|---|---|
| **Initial acknowledgement** | 3 business days | Whether we can reproduce / prioritize |
| **Status update** | every 7 days until patched | Or sooner if scope / fix changes |
| **Patch in a release** | 30 days (critical: 7 days) | Pull request merged into `main` |
| **Public disclosure** | after patch ships + 14 days (or coordinated with reporter) | via GHSA advisory + release notes |

Critical-severity issues (RCE / sandbox escape / credential exposure):
we accelerate the timeline. Low-severity issues (typos, misleading
docs) may be batched into a maintenance release.

## Supported versions

This project is at `Development Status :: 3 - Alpha`
(`pyproject.toml: classifiers[0]`). Per the [Python trove
classifiers](https://pypi.org/classifiers/) convention, **only the
latest tagged release receives security patches**. Pre-1.0 releases
are best-effort.

| Version | Supported |
|---|---|
| **0.1.x** (latest) | ✅ Yes (best-effort during alpha) |
| **0.0.x** | ❌ No — please upgrade |
| **`<unspecified>`** (unreleased `main`) | ⚠️ No formal support, but critical reports are reviewed |
| **any fork / vendored copy** | unsupported; please report to the fork owner |

We may backport security fixes to older versions on a case-by-case
basis if the impact is broad enough to justify the maintenance burden.

## Scope

### In scope

* **sandbox escapes** &mdash; circumventing `DockerSandbox` or
  `SubprocessSandbox` profile constraints (path, network, env,
  CPU/mem/disk/time budgets). See `src/seharness/sandbox/`.
* **orchestrator integrity** &mdash; phase transitions, idempotency
  keys, run-state corruption. See `src/seharness/orchestrator/`.
* **supply-chain attacks** &mdash; compromised upstream deps,
  malicious action versions, prompt-injection paths.
* **secrets in CI** &mdash; tokens or credentials exposed via
  workflows, logs, or artifacts.
* **telegram bot** &mdash; command-injection in `/feature` or
  webhook payloads. See `src/seharness/telegram_runtime/`.
* **observability redactor bypass** &mdash; unsanitized secrets in
  trace files. See `src/seharness/observability/redactor.py`.

### Out of scope

* **bugs without security impact** &mdash; please open a regular
  GitHub issue for non-security bugs.
* **theoretical attacks without a PoC** &mdash; we're happy to
  discuss the threat model, but won't open a CVE for a hypothetical.
* **third-party dependencies** that we don't control &mdash; please
  report upstream; we can coordinate with maintainers.
* **Denial of Service via pathologically large inputs** &mdash; only
  accepted as `P3` for the alpha; we'll fix if reproducible.

## Security-relevant configuration

* **Secrets**: never commit `.env`, secrets, or credentials. The
  `examples/controller.yaml` template uses placeholder env-var
  references (`${OPENAI_API_KEY}`) for safety. Use SOPS, age, or
  Vault in production.
* **GITHUB_TOKEN**: workflow files declare the *minimum* permissions
  they need (`contents: read`, `pages: write`, `id-token: write`).
  Adding `write` permissions requires a maintainer review.
* **Actions pinned by SHA**: tracked in G4 (Cluster G supply-chain
  hardening). Tag references (`@v3`) are noise; SHA references are
  load-bearing.

## Acknowledgements

We follow the [GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories/working-with-global-security-advisories-from-the-github-graphql-api)
disclosure process. Researchers who report valid vulnerabilities are
credited in the GHSA advisory unless they prefer to remain anonymous.

## Contact

* **GitHub Security Advisories**: https://github.com/johrenberger/software-engineering-harness/security/advisories/new
* **Maintainer**: `@johrenberger` (open issues or DMs via GitHub for
  non-sensitive questions)

Last updated: 2026-07-19 &mdash; see git history for the change log.
