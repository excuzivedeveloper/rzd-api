# Server Testing

Use a disposable Linux user and a clean virtual environment. Do not run these
checks on a WayFound production host, do not copy WayFound `.env` files, and do
not grant access to production databases, Docker socket, or unrelated SSH keys.

## Offline Preparation

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
python scripts/smoke_local.py
python scripts/verify_fork.py --run-tests
```

Expected properties:

- base install imports `rzd_api`;
- `mcp`, `uvicorn`, and `starlette` are not needed for base library use;
- `verify=False` is absent;
- `InsecureRequestWarning` is not globally suppressed;
- unit tests run without live RZD network access and without MCP tests.

## Optional Live Smoke

Run live smoke only once, manually, after the offline checks pass:

```sh
export RZD_LIVE_TEST=1
python scripts/smoke_live.py
unset RZD_LIVE_TEST
```

Expected library call count: 2. The script disables transport retries with
`retry_total=0`, so the HTTP attempt limit is 2: one station lookup and one
direct train search using public station codes. It does not book, buy,
authenticate, or use passenger personal data.

## Restrictions

- Do not start `rzd-mcp-server`.
- Do not install `.[mcp]` for base library validation.
- Do not publish port `8000`.
- Do not disable TLS verification.
- Do not set `RZD_CA_BUNDLE` to a secret-bearing file.
- Do not run repeated live smoke loops.

## Dedicated Codence CI — staged, **not activated** (2026-10-17)

The `.github/workflows/ci.yml` change is prepared for **static review only** on a
Draft PR against `wayfound-integration`. The separate repo-scoped `rzd-runner`
(UID 1003, systemd unit
`actions.runner.excuzivedeveloper-rzd-api.codence-rzd.service`) was reported by
Server Validation as installed but **inactive and disabled**. This document
neither authorizes starting it nor claims it is currently offline/online as
verified by GitHub, nor claims any end-to-end CI PASS.

All CI jobs target the exact labels
`[self-hosted, Linux, X64, rzd-api, codence-rzd]` (labels must be checked in
GitHub before activation). The `quality` job keeps Python 3.10–3.14 with
`max-parallel: 1`, isolated `uv`-managed interpreters/venvs, pytest + at least
90% coverage, repository-wide Ruff check **and** format, Mypy, build/Twine and
strict `pip-audit`. Separate jobs keep a full-history, fail-on-findings Gitleaks
scan and real-image rootless Podman build, loopback HTTP health and MCP protocol
initialization/tool-list regression. Trigger types remain internal
`pull_request`, `push` on `master` and `wayfound-integration`, and restricted
`workflow_dispatch`. The single dedicated runner slot and the five-version matrix
`max-parallel: 1` serialize execution without canceling other PR workflows. Cleanup only targets artifacts created by the current job;
no global prune or production host changes are authorized.

### Temporary trusted manual dispatch gate (2026-10-19)

For this pre-merge CI gate only, all three jobs also accept
`workflow_dispatch` on `refs/heads/ci/rzd-codence-dedicated-runner`, in
`excuzivedeveloper/rzd-api`, initiated by `excuzivedeveloper`. The same
repository/actor restrictions apply to manual dispatch on `master` and
`wayfound-integration`. Other dispatch refs, repositories and actors are denied
by the job conditions. No workflow inputs or arbitrary SHA/command execution
path are added. Retaining or removing this temporary branch allowance requires
a later owner-approved cleanup/merge decision.

The owner's proposed GitHub-side execution policy covers **all workflows**,
allows only actor `excuzivedeveloper`, and only events `push` and
`workflow_dispatch`. Its creation, **Active enforcement**, effective scope and
admission behavior have **not been independently verified**. When that policy is
effective, automatic `pull_request` CI is deliberately unavailable, including
internal PRs; the YAML trigger remains unchanged as defense in depth. A saved
form or an active workflow identity is not proof of an active execution policy.
Independent SERVER TEST must verify the effective policy, branch trust and
runner eligibility before activation. Keep the runner **OFF** meanwhile.

GitHub supports selecting a branch for an existing default-branch workflow.
The same `.github/workflows/ci.yml` exists on default branch `master` with
`workflow_dispatch`. After separate authorization, the trusted operator can
select this PR branch in the UI, or use the documented branch-specific CLI path:

```sh
# Future authorized operation only; not executed or authorized by this document.
gh workflow run ci.yml --repo excuzivedeveloper/rzd-api --ref ci/rzd-codence-dedicated-runner
```

Before dispatch, verify the trusted authenticated actor, PR #4 repository/ref
and reviewed HEAD. After dispatch, require the run's event, head branch and
`head_sha` to match `workflow_dispatch`, this exact branch and that reviewed
HEAD; any mismatch or branch advance is a STOP, not exact-head acceptance.
See [manual workflow runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[dispatch event semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
and [execution policies](https://docs.github.com/en/actions/how-tos/administer/control-workflow-execution).

Legacy run `39021303502` at pre-follow-up HEAD
`cce9a80301a6b745a38a72903c35916b1b854a38` was observed on 2026-10-19 with
four cancelled jobs, three queued and zero successes. It is incomplete and is
not acceptance evidence. This follow-up is static/offline validation only:
**CI_NOT_RUN / FULL_CI_PASS=NO**; runner state is based on the supplied SERVER
TEST checkpoint, not a new host observation. A new full exact-head run and
independent review of this security-relevant delta remain prerequisites.

### Blocking before runner activation: external trust policy

**Workflow `if:` tests are only defense in depth, not a fork-PR isolation
boundary:** a fork can modify workflow YAML in its own PR. The owner and an
independent Server Validation agent must verify the **effective GitHub repository
and organization Actions settings** before allowing this runner online:

1. Verify that the runner belongs to **this repository only**, with the exact
   required labels, correct dedicated account/service identity, and that the
   unit is still inactive/disabled. Runner name alone proves none of these.
2. Verify fork PR workflows cannot execute untrusted head code on this
   self-hosted runner, including when a fork modifies `.github/workflows/*`.
   Require an enforceable GitHub-side fork-approval/runner-group or trusted
   workflow policy; approval of unknown external PRs must not automatically
   grant self-hosted execution. If this cannot be established, **activation is
   blocked**. Internal PRs must be limited to trusted writers and reviewed
   workflow changes.
3. Verify default and workflow `GITHUB_TOKEN` permissions are read-only, secrets
   are not exposed to PR jobs, and checkout uses `persist-credentials: false`.
   Confirm branch protection/rulesets and review requirements are consistent
   with this admission model. Do not change repository security settings as a
   side effect of verification.
4. Confirm runner host `/home/rzd-runner/.local/bin/uv`, Python 3.10–3.14,
   Gitleaks, rootless Podman and `/run/user/1003` are accessible **as UID 1003**;
   ensure workspace isolation, free disk, memory/service cgroup limits and no
   access to other services, their secrets or privileged container sockets.
   The existing one-CPU, ~1.9 GiB host has not run this exact real-image job.
5. Have an independent Reviewer accept the static workflow/security review.
   Only then may Orchestrator obtain separate explicit owner authorization to
   enable/start **this exact unit** and obtain a *single* trusted, explicitly
   authorized manual exact-head CI acceptance run with the ref/SHA verification
   above, classify failures, and disable/stop safely on unexpected state. Runner
   activation is not authorized by this PR or doc.

The validation may legitimately report baseline-identical repository Ruff /
format findings or the pre-existing optional `mcp>=1.28.1,<1.29` dependency
security finding; **neither is waived**. A disabled runner and queued jobs are
**NOT RUN**, not green checks. GitHub Actions, Python matrix, Gitleaks, rootless
image, health and MCP remain **unproven** until actually executed at the exact
commit. This staged change does not touch WayFound or production and does not
prove live RZD/provider behavior.
