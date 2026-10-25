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

## Offline MMP stage trace prototype

`scripts/mmp_stage_trace.py` is an opt-in diagnostic adapter for a **separate,
one-shot process**. It sends no request by itself and is never installed by
`RzdTransport` or the MCP server. Validate it only with local HTTPS origin and
HTTP CONNECT proxy fixtures:

```sh
python -m pytest -q tests/test_mmp_stage_trace.py
```

The trace contains allowlisted stage states and coarse monotonic time buckets:
effective proxy source (`explicit`, `environment` or `unknown`), TCP connect,
proxy CONNECT, target TLS, request write, first response headers, final status
class and failure family. It does not retain or print URLs, proxy addresses,
headers, cookies, route identifiers, bodies, exception text or trace IDs. The
adapter preserves the existing Requests request and retry configuration; a
future separately authorized live run must explicitly set `retry_total=0` and
disable redirects to bound wire attempts. The helper refuses any Requests /
urllib3 pair other than the locally validated 2.34.2 / 2.8.0. It uses
per-adapter urllib3 connection-pool subclassing and therefore needs independent
HIGH-risk review before a live experiment.

With an HTTPS CONNECT tunnel, `proxy_connect=done` and `target_tls=done` still
cannot distinguish a proxy relay stall from an RZD endpoint stall after request
write. A timeout is a client observation, not proof that the upstream received
or processed the POST. A pooled connection or redirect produces multiple stage
events; the intended use is a fresh session and a single, no-retry call. No
production or provider test is authorized by this document.

### D12 one-shot enforcement (offline correction; live remains gated)

`scripts/mmp_one_shot.py` adds a disposable supervisor/worker; normal library
entrypoints never import it. Review and test with
`python -m pytest tests/ -m 'not integration'`. Only tests substitute a localhost
endpoint; the CLI has no endpoint/method/timeout override. It sends the approved
body once through the unchanged `RzdTransport.request_json` serializer, with
MMP-only `LANG_SITE=ru`, a fresh Session, verified CA, connect/read 5/12 seconds,
and total/connect/read/status retries zero. Redirects are disabled at request
and send boundaries; a 3xx is terminal STOP. Duplicate Session, adapter or
low-level connection/write entries fail before a second network operation.

The supported topology is an explicit **HTTP CONNECT proxy**, with its original
credentials, and HTTPS target verification. Missing proxy, SOCKS/HTTPS proxy,
ambient proxy discovery or proxy/CA environment settings cause zero-send STOP.
`trust_env` remains enabled; environment is never repaired automatically.
`SSLKEYLOGFILE` also causes STOP so TLS tracing cannot write a secret file.
Prepared Authorization (including netrc injection) is rejected. No requests to
station lookup or fallback endpoints are made.

Future invocation is `python -B -m scripts.mmp_one_shot < protected-input.json`
from the exact clean reviewed checkout and its isolated venv. **Do not run it
now.** No fixture, proxy, credentials or CA path belongs on the command line or
in shell tracing. The preexisting protected input must contain exactly
`config` (keys `proxy`, `ca_bundle`, `user_agent`, `referer`; null uses stock
defaults) and `fixture` (key `body`, the previously authorized nonpersonal B12B
JSON object, reused verbatim). Stdin must be a regular file <=16 KiB. This
document deliberately supplies no real fixture or live invocation block.

All gates are required: exact Requests 2.34.2 / urllib3 2.8.0;
`RZD_LIVE_TEST=1`, `RZD_MMP_PREFLIGHT_OK=1`, `RZD_MMP_BUDGET_OK=1`;
`RZD_MMP_REVIEWED_SHA` equal to the independently approved 40-character HEAD;
and `RZD_MMP_FIXTURE_SHA256` equal to the **previously approved** fixture digest
(SHA256 of UTF-8 `json.dumps(fixture, sort_keys=True, separators=(',', ':'))`).
These flags attest completed OWNER-mediated preflight/budget/review; they do
not independently establish approval. Do not generate a replacement fixture or
set flags merely to bypass STOP. Missing approved input means HOLD.

The supervisor redirects OS stdout/stderr to the null device before spawning,
disables logging and HTTP debug, and kills the worker at the independent
30-second deadline even if body bytes keep arriving. It accepts <=4 KiB of
validated IPC; malformed/absent output, nonzero exit and count mismatch are
STOP. Only a validated fixed-category trace summary plus `guard_verdict`,
`attempt_count=0|1`, and `redirect_count=0` reaches stdout. Timeout reports use
unknown stages and the shared counter set immediately before adapter network
entry. This counts client attempts, **not provider receipts**. PASS means the
enforcement completed, not HTTP/application success or production recovery.
Exceptions and cleanup errors never print their text. The harness writes no
trace files and changes no production config, services or request defaults.

`stage_elapsed` remains **cumulative since attachment**, not per-stage latency
or the supervisor deadline. CONNECT/TLS/local write success cannot prove RZD
handled the POST or separate proxy relay from origin failure. Any subsequent
live run still requires exact-final-HEAD independent HIGH-risk PASS, sufficient
CI and the D12 preflight. This offline correction authorizes no live request,
merge, deployment, restart or production change.

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

## Proposed GitHub-hosted CI — observed manual runs (2026-10-23)

The migration branch proposes standard `ubuntu-24.04` GitHub-hosted VMs for
all three CI jobs. It is based on accepted combined PR #4 head
`2fd9c1e3ce93a768f37ca6b31a42d9ff9cd2ed9d`, prepared on an isolated branch;
this does not change PR #4 or authorize integration, dispatch or merge.
**RUNNER=NOT ACTIVATED; FULL_CI_PASS=NO.** The former Codence runner is not
needed by this proposal and must not be started or changed by this task.

The `quality` job keeps Python 3.10–3.14, `max-parallel: 1` and the existing
job/check names. Official `actions/setup-python` v7.0.0 is pinned to
`5fda3b95a4ea91299a34e894583c3862153e4b97`; each job creates a temporary
venv and asserts the exact Python major/minor. Gates remain offline pytest
(`tests/ -m 'not integration'`) with branch coverage >=90%, Ruff check **and**
format, Mypy, source `git archive` build/Twine and strict `pip-audit` on the
installed dev graph. The container and test client still use distinct graphs:
Dockerfile installs constrained `.[mcp]` on Python 3.12, while quality and
client jobs install unconstrained `.[dev]`. Runtime pins are unchanged.

The `secrets` job checks out full history (`fetch-depth: 0`) and downloads
[Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)
from its upstream Linux x64 release. Before extraction/execution it checks the
archive against the fixed SHA-256
`551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`
(published upstream checksum/release-asset digest). The scan retains
`git --redact --no-banner --exit-code=1 --log-opts='--all' .`, with no
HEAD-only narrowing or allow-fail. The repository-root `.gitleaksignore`
contains only the 42 full fingerprints (commit, fixture path, rule and line)
for the independently reviewed historical transfer-response identifiers.
All other findings still fail the job. This scoped exception does not remove
the old values from Git history or establish that opaque route identifiers
could never have capability semantics; GitHub-managed PR refs/caches remain a
separate exposure question.

The `docker` job uses stock Docker Engine on the fresh GitHub VM: actual
Dockerfile build, bridge networking, ephemeral host port on **127.0.0.1 only**,
384 MiB memory / 512 MiB memory+swap, 64 PIDs, all capabilities dropped and
`no-new-privileges`. Health polling and the authenticated existing MCP
initialize/list-tools/invalid-query regression remain mandatory. The CI token
is synthetic. Docker daemon access is privileged within this disposable VM;
the image itself still runs as its unchanged non-root Dockerfile user.
The old Podman build's 512 MiB flag is not carried to modern Docker BuildKit,
which has different build resource controls; the build uses hosted VM limits,
while all container runtime bounds above remain enforced. No legacy-builder
fallback, extra service or host configuration is introduced. Cleanup uses
Docker inspect/rm/rmi only for guarded job-owned names, plus its own venv.
No global prune, Codence/NLServer Docker access or service operation is allowed.

Checkout remains pinned with `persist-credentials: false`; token permissions
remain `contents: read`. The `push` and `pull_request` triggers and exact
repository/actor guards remain unchanged. In all three jobs, the owner-only
`workflow_dispatch` allowance now names the reviewed migration branch
`ci/rzd-github-hosted-migration-20261021` in place of the retired Codence branch.
A push to this branch matches no CI push branch filter. This change does not
launch a workflow or authorize dispatch. Other repository workflows retain
their existing schedule/manual/release triggers.
Owner-provided GitHub policy screenshots confirm active `RZD CI Trusted
Execution` enforcement for all workflows: only actor `excuzivedeveloper` and
events `push` and `workflow_dispatch` are allowed. `pull_request` is excluded.
The observed PR #6 `pull_request` run [#39777759439](https://github.com/excuzivedeveloper/rzd-api/actions/runs/39777759439)
ended with `startup_failure` and zero jobs. The event restriction is sufficient
to explain its rejection; no deeper platform annotation was available. An
OWNER-authorized manual `workflow_dispatch`
[#39803799244](https://github.com/excuzivedeveloper/rzd-api/actions/runs/39803799244)
at `e255175048340d8ccc5e8abd3aef15dfb7ff84b1` did start all seven jobs:
Python 3.10–3.14 and Docker/MCP passed (six jobs), while full-history Gitleaks
failed on 42 findings in the historical transfer fixture (one job). Thus
**FULL_CI_PASS=NO** at that old HEAD. A later OWNER-authorized manual
`workflow_dispatch` [#39948867953](https://github.com/excuzivedeveloper/rzd-api/actions/runs/39948867953)
at `b6e4a15a9524e9ab23bc047a3e3a9efbebc51e57` completed **7/7 PASS**,
including the full-history Gitleaks job. That success applies only to the
pre-LANG_SITE-integration HEAD; the corrected HEAD still needs its own
separately authorized CI validation. A future automatic internal-PR CI run
requires a separate OWNER-approved Actions event-policy change to permit
`pull_request` while preserving trusted-actor restrictions. This document does
not authorize that settings change or another dispatch. Any later validation
must match the exact reviewed branch HEAD and pass all required jobs.

Standard hosted runner minutes for this **public rzd-api repository** are free;
artifact/cache storage and other billing limits still apply. This does not
claim free hosted execution for private WayFound. See GitHub's
[runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
and [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Any further diagnostic exact-HEAD `workflow_dispatch` on
`ci/rzd-github-hosted-migration-20261021` requires independent review and
separate OWNER authorization. Before merge or integration, reconcile
the overlapping PR #4/#5 changes, refresh the minimum affected history map,
and preserve independent security/history findings, including fail-on-findings
full-history Gitleaks. The parallel six-branch private history rehearsal and
WayFound dependency pin remain separately gated. No history sandbox,
production deployment, NLServer state or live-provider validation is changed
or newly verified here.

## Historical Codence CI preparation — not the proposed architecture

The following dated record describes the former proposal at/before PR #4 head
`2fd9c1e3ce93a768f37ca6b31a42d9ff9cd2ed9d`. Its dedicated-runner labels,
AppArmor/uv provisioning and activation checklist are historical context;
they are not requirements or activation authority for the hosted proposal above.
All old SHA references and validation limits below retain their original meaning.

### Dedicated Codence CI — staged, **not activated** (2026-10-17)

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

#### Local toolchain compatibility follow-up (2026-10-21)

The owner-accepted SERVER TEST checkpoint dated 2026-10-20 reports
**SERVER_LOCAL_TOOLCHAIN=PASS_WITH_LIMITS**: managed Python 3.10–3.14 discovery
worked under UID 1003 with a clean process environment and `uv --no-config`.
It also reports that `aa-exec -p podman -- podman info` permits rootless user
namespaces under the existing hardened AppArmor configuration, while plain
Podman is denied. These are reported local observations, not new Coder host
checks, runner admission evidence or exact combined-head CI acceptance.
The runner remains **OFF as reported**; the real image/MCP was not run in that
local workaround check, and approximately 1.2 GiB available RAM leaves resource
risk for the future image job.

Both workflow interpreter/venv setup steps now invoke the dedicated absolute
uv binary through `env -i`, with only the intended
`HOME=/home/rzd-runner`, `PATH=/usr/bin:/bin` and
`UV_PYTHON_INSTALL_DIR=/home/rzd-runner/.local/share/uv/python`.
Each invocation includes `--no-config`; managed discovery and both venv
creations retain `--no-python-downloads`. This removes inherited uv/Python,
virtualenv and XDG overrides from uv processes without changing host
configuration. Existing interpreter-version assertions remain mandatory.

All Podman operations use `aa-exec -p podman -- podman`, including build,
run, port/inspect probes, container/image existence checks and cleanup.
The profile must already exist and allow the operation; there is no plain
Podman/rootful fallback or host policy adjustment. UID 1003, rootless setup,
loopback publication, resource/security flags and job-owned artifact guards
remain unchanged. Static/mocked routing checks do not prove real AppArmor,
Podman build or resource compatibility; independent review and separately
authorized final combined-head CI are still required.

A new commit on PR #4 invalidates the private rewrite sandbox's old exact
SHA map for this branch. In particular, do not reuse
`434bf68075dfce27cd450bd76c190e8cd8436738` as a future published PR #4 HEAD.
After the final reviewed commit, Orchestrator may request one bounded refresh
of the affected graph/diff. This follow-up neither changes that sandbox nor
authorizes history publication; preserve recovery material, original PRs,
master and the existing WayFound pin pending a separate decision.
Historical credential authenticity/revocation remains **UNKNOWN**;
GitHub-managed PR refs are a separate exposure/retention issue.

#### Temporary trusted manual dispatch gate (2026-10-19)

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

#### Blocking before runner activation: external trust policy

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

On 2026-10-21, this PR #4 branch incorporated the two reviewed PR #5
source/fixture/dependency commits from head
`c922fadd3d00af57cd7d51ea78b0e1f24c1ce4fe`. The eight accepted source paths
are identical to that head; the accepted PR #4 workflow is preserved.
Earlier PR #5 local tests/audit PASS remains evidence for its original exact
head, not full CI acceptance of this combined tree. No finding is waived.
The container installs `.[mcp]` with the Python 3.12 runtime constraints;
quality matrix and MCP-client jobs resolve `.[dev]` without those constraints.
These are distinct dependency graphs. A clean Windows Python 3.12 dev install
and strict audit on 2026-10-21 provide local evidence only; the future Linux
matrix and actual constrained image still require exact-head CI validation.
Do not apply the image lock indiscriminately across Python 3.10–3.14.
A disabled runner and queued jobs are
**NOT RUN**, not green checks. GitHub Actions, Python matrix, Gitleaks, rootless
image, health and MCP remain **unproven** until actually executed at the exact
commit. This staged change does not touch WayFound or production and does not
prove live RZD/provider behavior.
