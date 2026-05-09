# Phase 7 – CI/CD Pipeline (quality gates + image-based deploy)

## Context

- Branch: `feature/phase-7-cicd` (already created and currently checked out).
- Prior phases delivered: Docker (Phase 1), APScheduler (Phase 2), pytest two-tier suite (Phase 3), PostgreSQL via SQLAlchemy + Alembic (Phase 4), FastAPI + Telegram split (Phase 5), `ruff` lint + format inside Docker (Phase 6).
- **The existing deploy is broken.** `~/deploy_BoTC.sh` on the VPS was written for the pre-Phase-5 single-container architecture (no separate `telegram` service, no `postgres` service, no `uvicorn` entrypoint). It cannot deploy the current code as-is. The current `.github/workflows/deploy.yml` runs only on `push: main` and SSHes into the VPS to invoke that broken script. Phase 7 replaces both halves of this in one PR.
- **Goal of this phase**: ship a single unified pipeline (`ci.yml`) that gates quality on every PR, builds and publishes a container image on every push to `main`, and deploys that image to the VPS — so subsequent phases can be developed by committing directly to `main` without needing PR ceremony, while CI enforces correctness and the deploy step only runs after every gate passes.
- Repo identity (verified at planning time): `github.com/jAjiz/BoTC`. GHCR image will be `ghcr.io/jajiz/botc` (GHCR namespaces are lowercase).
- Relevant files to read before starting:
  - `ROADMAP.md` — Phase 7 scope (authoritative)
  - `.github/workflows/deploy.yml` — current pipeline, will be **deleted**
  - `Dockerfile` — multi-stage; `INSTALL_DEV=true` build arg installs `requirements-dev.txt` (pytest + ruff). Production image is `INSTALL_DEV=false` (default)
  - `docker-compose.yml` — bot + telegram + postgres; both app services build the same image with different `command:` directives. Image will be replaced by `image:` in the prod override
  - `docker-compose.test.yml` — single `test` service used in CI for lint/unit/integration
  - `tests/integration/test_integration.py` — integration suite, gated by `RUN_KRAKEN_INTEGRATION` and `RUN_DB_INTEGRATION` env vars
  - `pyproject.toml` — owns the `--cov-fail-under=80` gate
  - `scripts/entrypoint.sh` — runs `alembic upgrade head` before the CMD; works identically against a pulled image
  - `README.md` — Quick Start, Testing, Code quality, Infrastructure sections (will receive badges + new CI/CD section)
- Architectural decisions:
  - **One image, two services.** `botc` and `telegram` already share `Dockerfile` and differ only in `command:`. CI builds and pushes a single image; the VPS uses one tag for both services.
  - **GHCR over Docker Hub.** GitHub Container Registry authenticates via the workflow's built-in `GITHUB_TOKEN` for pushes — no extra account, no PAT stored in repo secrets. The GHCR package will be made **public** after the first push, so the VPS pulls without authentication. (The fallback path — keeping the package private and storing a `read:packages` PAT on the VPS — is documented but not the default.)
  - **Tag scheme**: every CI build tags the image with both `:sha-<short>` (immutable, traceable) and `:main` (moving, points at current `main`). The VPS pulls `:main`. Rollbacks pin to a `:sha-<short>` explicitly.
  - **Production compose override**, not a separate file.** A new `docker-compose.prod.yml` only overrides `image:` and removes the `build:` block for `botc` and `telegram`. The existing `docker-compose.yml` stays buildable for local development.
  - **One workflow, not two.** A single `ci.yml` defines `lint`, `unit`, `integration`, `build-and-push`, and `deploy` jobs with `needs:` dependencies. The build/deploy jobs are gated to `push: main` via `if:`. This removes the `workflow_run` indirection from the previous draft and keeps the entire pipeline visible in one file.
  - **Solo-developer flow.** Once Phase 7 lands, subsequent phases are developed by committing directly to `main`. The `needs:` chain in `ci.yml` is the enforced gate: a failing test blocks the deploy job from running, regardless of how the commit reached `main`. Branch protection is **optional** and called out as such in the README.
  - **The VPS becomes a thin runtime.** It holds only a deploy directory with `.env` and the two compose files — no repo clone, no source code. The deploy job fetches `docker-compose.yml` and `docker-compose.prod.yml` directly from `raw.githubusercontent.com` at the exact commit SHA being deployed, then runs `docker compose pull && up -d`. Disk footprint on the VPS is two text files plus Docker images and the PostgreSQL volume.

## Target outcome

```
PR opened (any target branch)
  └─ ci.yml runs: lint, unit, integration  (in parallel)

Push to main (PR merge or direct push)
  └─ ci.yml runs:
       lint, unit, integration  (parallel)
         └─ build-and-push  (builds prod image, tags :sha-<short> and :main, pushes to GHCR)
              └─ deploy  (SSHes to VPS, git pulls compose files, docker compose pull + up -d)

Result: ghcr.io/jajiz/botc:main always points at the image currently running on the VPS,
        and ghcr.io/jajiz/botc:sha-<short> is permanently retrievable for rollback.
```

A failing lint or test on `main` short-circuits before any image is pushed and before any SSH happens. The VPS keeps running the previously-deployed image.

---

## Step 0 — Manual prerequisites (you do these out-of-band)

These steps are not automatable from the workflow. Complete them before merging Phase 7, in this order. Each is a one-time setup; later phases will not need to repeat them.

### 0.1 VPS: stop the old deploy and prepare the new layout

SSH into the VPS as your deploy user.

```bash
# 1. Stop whatever is currently running.
docker ps                          # confirm what's running first
docker compose down || true        # if there's a compose project, tear it down
# If you previously ran the bot directly with `docker run`, kill those containers manually.

# 2. Move the old broken deploy script aside (don't delete yet — we may want to read it).
mv ~/deploy_BoTC.sh ~/deploy_BoTC.sh.bak 2>/dev/null || true

# 3. Create the deploy directory. Only compose files and .env live here — no repo clone.
mkdir -p ~/BoTC

# 4. Place the production .env file at ~/BoTC/.env.
#    Use whatever channel you trust (scp, paste from password manager, etc.).
#    Required keys: KRAKEN_API_KEY, KRAKEN_API_SECRET, TELEGRAM_BOT_TOKEN,
#    TELEGRAM_CHAT_ID, POSTGRES_PASSWORD, plus any others currently in use.
ls -la ~/BoTC/.env                 # must exist and be readable by the deploy user

# 5. Copy legacy CSV and JSON data files to the VPS (one-time, for Step 6 data migration).

# 6. Verify Docker + Compose plugin are installed.
docker --version
docker compose version

# 7. Verify the deploy user can run docker without sudo.
docker ps
# If this fails: `sudo usermod -aG docker $USER` then log out/in.
```

Do **not** run `docker compose up` yet — the prod compose override doesn't exist in `main` yet, and the GHCR package doesn't exist yet. The first deploy is driven by the workflow.

### 0.2 GitHub: workflow secrets

Repository → Settings → Secrets and variables → Actions. Confirm these secrets exist (they were already configured for the old `deploy.yml`):

- `VM_IP` — VPS public IP or hostname
- `VM_USER` — SSH user with docker access
- `VM_KEY` — SSH private key matching the user

Add **one new secret**:

- `VM_DEPLOY_PATH` — absolute path on the VPS where the repo is cloned. Use `/home/<vm-user>/BoTC` (the path you chose in Step 0.1). Storing this as a secret keeps host-specific paths out of the workflow file.

No GHCR credentials are needed — the workflow uses the built-in `GITHUB_TOKEN` to push, and the VPS pulls the public package anonymously.

### 0.3 GitHub: GHCR package visibility (after first push)

The GHCR package `ghcr.io/jajiz/botc` does not exist until the first successful `build-and-push` run. After that run completes:

1. Go to https://github.com/jAjiz?tab=packages → `botc` → Package settings.
2. Under "Danger Zone" → "Change visibility" → set to **Public**.
3. Under "Manage Actions access" → confirm the `BoTC` repo has `Write` access (it should, by default, since the package was created from this repo's workflow).

If you prefer to keep the package private, see the appendix at the end of this plan for the PAT-based VPS auth setup. The default path is public.

### 0.4 GitHub: branch protection (optional, recommended)

For solo-developer use, the `needs:` chain in `ci.yml` already prevents broken builds from deploying. Branch protection is optional. If you want to add it anyway as a belt-and-braces measure:

Repository → Settings → Branches → Add rule → Branch name pattern `main`:
- ✅ Require status checks to pass before merging
- Required checks (must match the `name:` fields in `ci.yml` exactly): `Lint (ruff)`, `Unit tests`, `Integration tests`
- ❌ Do **not** check "Require a pull request before merging" — that would block your direct-to-main workflow.

Skip this entirely if you don't want it.

### 0.5 Decide whether to keep the old SSH script around

`~/deploy_BoTC.sh.bak` (renamed in Step 0.1) is no longer called by anything. Delete it once you've verified the first deploy works end-to-end (Step 6 below). Keeping it temporarily is harmless.

---

## Step 1 — Production compose override

Create `docker-compose.prod.yml` at the repository root. It overrides only `botc` and `telegram` to use a published image instead of building from source. Everything else (postgres, networks, volumes) is inherited from the base `docker-compose.yml`.

```yaml
# Production override. Used on the VPS as:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml <up|pull|...>
#
# IMAGE_TAG defaults to `main` (the moving tag pushed by CI on every main build).
# Set IMAGE_TAG=sha-<short> in the deploy environment to pin a specific build for
# rollback.

services:
  botc:
    image: ghcr.io/jajiz/botc:${IMAGE_TAG:-main}
    build: !reset null

  telegram:
    image: ghcr.io/jajiz/botc:${IMAGE_TAG:-main}
    build: !reset null
```

Notes:
- `!reset null` is Compose v2.20+ syntax that explicitly removes the inherited `build:` block. Without it, Compose would still try to build the local Dockerfile alongside pulling. If the VPS's Compose version doesn't support `!reset`, fall back to omitting it — `image:` will take precedence over `build:` for `compose pull` / `compose up` even when both are present, but `compose build` would still build from source. For our flow this is fine because the VPS only ever runs `pull` and `up`, not `build`.
- The `${IMAGE_TAG:-main}` interpolation lets you do `IMAGE_TAG=sha-abc1234 docker compose ... up -d` for a deterministic rollback without editing files.
- The image name uses lowercase `jajiz` because GHCR normalizes namespaces to lowercase. Don't write `jAjiz` here.

**Commit:** `feat(compose): add docker-compose.prod.yml override for image-based deploy`.

---

## Step 2 — Unified `ci.yml` workflow

Create `.github/workflows/ci.yml`. Five jobs with `needs:` dependencies forming a pipeline that fans out for tests and fans back in for build → deploy.

### 2.1 Triggers and top-level config

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read
```

`pull_request` is intentionally unfiltered so PRs targeting any branch are gated. `push: main` is the deploy precondition. The default `permissions:` is read-only; the `build-and-push` job overrides it to add `packages: write`.

### 2.2 Job: `lint`

```yaml
jobs:
  lint:
    name: Lint (ruff)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@<sha> # v4

      - name: Build dev image
        run: docker compose -f docker-compose.test.yml build

      - name: ruff check
        run: docker compose -f docker-compose.test.yml run --rm test ruff check .

      - name: ruff format --check
        run: docker compose -f docker-compose.test.yml run --rm test ruff format --check .
```

### 2.3 Job: `unit`

```yaml
  unit:
    name: Unit tests
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@<sha> # v4

      - name: Build dev image
        run: docker compose -f docker-compose.test.yml build

      - name: Run unit tests
        run: docker compose -f docker-compose.test.yml run --rm test pytest tests/unit
```

The `--cov-fail-under=80` flag is in `pyproject.toml`, so coverage is enforced automatically.

### 2.4 Job: `integration`

Postgres runs as a compose service inside `docker-compose.test.yml` so the test container reaches it by hostname over the shared Docker network — no `--network host` or GH Actions `services:` block needed. `docker compose run` does not support `--network` as a flag.

```yaml
  integration:
    name: Integration tests
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@<sha> # v4

      - name: Build dev image
        run: docker compose -f docker-compose.test.yml build

      - name: Start postgres
        run: docker compose -f docker-compose.test.yml up -d --wait postgres

      - name: Apply Alembic migrations
        run: |
          docker compose -f docker-compose.test.yml run --rm \
            -e POSTGRES_PASSWORD=botc \
            test alembic upgrade head

      - name: Run integration tests
        run: |
          docker compose -f docker-compose.test.yml run --rm \
            -e POSTGRES_PASSWORD=botc \
            -e RUN_DB_INTEGRATION=true \
            test pytest tests/integration

      - name: Tear down
        if: always()
        run: docker compose -f docker-compose.test.yml down -v
```

Implementation notes:
- `--wait` blocks until the postgres healthcheck passes (requires Compose v2.1.1+, available on `ubuntu-latest`).
- `RUN_KRAKEN_INTEGRATION` is **not** set — those tests skip with a clean message. We do not put live Kraken credentials in CI.

### 2.5 Job: `build-and-push`

```yaml
  build-and-push:
    name: Build and push image
    needs: [lint, unit, integration]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    outputs:
      image_tag: ${{ steps.meta.outputs.image_tag }}
    steps:
      - name: Checkout
        uses: actions/checkout@<sha> # v4

      - name: Compute image tag
        id: meta
        run: echo "image_tag=sha-$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Log in to GHCR
        uses: docker/login-action@<sha> # v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@<sha> # v6
        with:
          context: .
          file: Dockerfile
          push: true
          tags: |
            ghcr.io/jajiz/botc:${{ steps.meta.outputs.image_tag }}
            ghcr.io/jajiz/botc:main
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Implementation notes:
- Two new pinned actions: `docker/login-action` and `docker/build-push-action`. Pin both to commit SHAs (look up at execution time).
- `cache-from: type=gha` / `cache-to: type=gha,mode=max` is justified here (unlike in the test jobs) because the production image is the actual deploy artifact — fast rebuilds matter more for the hot path.
- The image is built from `INSTALL_DEV=false` (the Dockerfile default) — the production image does **not** include pytest or ruff. Verify by inspecting the pushed image size after the first run.
- The `image_tag` job output is consumed by the `deploy` job below.

### 2.6 Job: `deploy`

```yaml
  deploy:
    name: Deploy to VPS
    needs: [build-and-push]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - name: SSH and roll out
        uses: appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2 # v1.2.5
        with:
          host: ${{ secrets.VM_IP }}
          username: ${{ secrets.VM_USER }}
          key: ${{ secrets.VM_KEY }}
          envs: DEPLOY_PATH,IMAGE_TAG,COMMIT_SHA
          script: |
            set -euo pipefail
            mkdir -p "$DEPLOY_PATH"
            curl -fsSL "https://raw.githubusercontent.com/jAjiz/BoTC/${COMMIT_SHA}/docker-compose.yml" \
              -o "$DEPLOY_PATH/docker-compose.yml"
            curl -fsSL "https://raw.githubusercontent.com/jAjiz/BoTC/${COMMIT_SHA}/docker-compose.prod.yml" \
              -o "$DEPLOY_PATH/docker-compose.prod.yml"
            cd "$DEPLOY_PATH"
            docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
            docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --remove-orphans
            docker image prune -f
        env:
          DEPLOY_PATH: ${{ secrets.VM_DEPLOY_PATH }}
          IMAGE_TAG: ${{ needs.build-and-push.outputs.image_tag }}
          COMMIT_SHA: ${{ github.sha }}
```

Implementation notes:
- `COMMIT_SHA` pins the curl fetch to the exact commit being deployed, not the moving `main` ref. This means the compose files on the VPS always match the image being pulled.
- `IMAGE_TAG` is passed but not consumed by the script (the compose file defaults to `:main`). It is included so a manual rollback step can `IMAGE_TAG=sha-abc1234` without changing the workflow. Documented in Step 5 below.
- `docker image prune -f` cleans up the previously-deployed image. Running daily on the VPS would be tidier, but adding it here keeps the cleanup tied to the deploy lifecycle.

**Commit:** `ci(workflows): add unified ci.yml with lint, unit, integration, build, and deploy`.

---

## Step 3 — Delete the old `deploy.yml`

```
git rm .github/workflows/deploy.yml
```

The old workflow's behaviour is fully subsumed by `ci.yml`'s `deploy` job, and leaving it in place would cause two SSH connections per push.

**Commit:** `ci(workflows): remove old deploy.yml — superseded by unified ci.yml`.

---

## Step 4 — README updates

### 4.1 Top-of-file badges

Add two badges immediately under the H1, before the introductory paragraph:

```markdown
# BoTCoin - Autonomous Digital Asset Manager

[![CI](https://github.com/jAjiz/BoTC/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jAjiz/BoTC/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)

BoTCoin is a 24/7 autonomous digital asset management system…
```

### 4.2 Replace the existing CI/CD section

Replace the existing `**CI/CD Pipeline** (.github/workflows/deploy.yml)` block in the Infrastructure section with a new **Continuous integration and deployment** section that documents the unified pipeline:

```markdown
### Continuous integration and deployment

A single workflow (`.github/workflows/ci.yml`) runs on every PR and every push to `main`:

| Job | When | What |
|---|---|---|
| `Lint (ruff)` | always | `ruff check` + `ruff format --check` inside the dev image |
| `Unit tests` | always | `pytest tests/unit` with the 80% coverage gate |
| `Integration tests` | always | `pytest tests/integration` against an ephemeral Postgres service (Kraken-gated tests are skipped in CI) |
| `Build and push image` | `push: main` only | Builds the production image and publishes it to `ghcr.io/jajiz/botc:main` and `ghcr.io/jajiz/botc:sha-<short>` |
| `Deploy to VPS` | `push: main` only | SSHes to the VPS, fetches the two compose files by commit SHA, and runs `docker compose pull && up -d` |

The `needs:` chain in the workflow file enforces ordering: a failing lint/test job blocks the image push and the deploy. Branch protection on `main` is optional — the pipeline gate is the workflow's job graph, not the branch rule.

To roll back to a previous image without reverting the commit:

    # On the VPS
    cd ~/BoTC
    IMAGE_TAG=sha-abc1234 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**Commit:** `docs(readme): add CI badges and document the unified CI/CD pipeline`.

---

## Step 5 — Verification

### 5.1 Local verification (before opening the PR)

Inside Docker:

```
docker compose -f docker-compose.test.yml build
docker compose -f docker-compose.test.yml run --rm test ruff check .
docker compose -f docker-compose.test.yml run --rm test ruff format --check .
docker compose -f docker-compose.test.yml run --rm test pytest tests/unit
```

Verify the prod compose override parses:

```
docker compose -f docker-compose.yml -f docker-compose.prod.yml config
```

The `image:` lines should resolve to `ghcr.io/jajiz/botc:main` and the `build:` blocks for `botc` and `telegram` should be absent from the rendered config.

### 5.2 Push the branch and observe the PR run

Open a PR from `feature/phase-7-cicd` to `main`. The PR run executes only `lint`, `unit`, `integration` (the build/deploy jobs are gated by `push: main`). All three must go green.

### 5.3 Merge → first deploy

Merging the PR triggers a `push: main` event, which runs the full pipeline:
- `lint`, `unit`, `integration` again (parallel)
- `build-and-push` (publishes `ghcr.io/jajiz/botc:main` and the SHA tag)
- `deploy` (SSHes to the VPS)

After the run completes, on the VPS:

```bash
docker ps                                   # botc + telegram + postgres all running
docker image inspect ghcr.io/jajiz/botc:main --format '{{.Id}} {{index .RepoDigests 0}}'
curl -s http://localhost:8000/status        # paused: false, last_run_at after first tick
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 50 botc
```

If the GHCR package is still private at this point, the VPS pull will fail with `denied: denied`. Complete Step 0.3 (make the package public) and re-run the workflow from the Actions tab.

### 5.4 Failure-mode checks

Push two throwaway commits to a scratch branch and observe:

1. Introduce a deliberate `ruff` violation. Confirm the `lint` job fails on the PR and `build-and-push` does not run. Revert.
2. Introduce a deliberate failing assertion in a unit test. Confirm the `unit` job fails, `build-and-push` does not run, and the previously-deployed image keeps running on the VPS. Revert.

---

## Step 6 — First-deploy bootstrap (after merging Phase 7)

After the merged Phase 7 commit triggers the first full pipeline run and the deploy job has completed:

1. **Verify the package was created**: https://github.com/jAjiz?tab=packages should now show `botc`.
2. **Make the package public** (Step 0.3 — once-only).
3. **If the deploy job failed because the package was private at first deploy time**, re-run the failed `deploy` job from the Actions tab (Re-run failed jobs). It will succeed once the package is public.
4. **Run the legacy data migration** (one-time — imports CSV/JSON into PostgreSQL):

   The production image contains `scripts/load_legacy_data.py` and `core.database`. Use `docker compose run` so the container shares the running network and can reach the `postgres` service by hostname.

   ```bash
   # On the VPS
   cd ~/BoTC
   docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm \
     -v ~/data:/app/data \
     botc \
     python scripts/load_legacy_data.py --data-dir /app/data --yes
   ```

   Expected output: one log line per CSV file processed, then a `trailing_state.json` line, then `Import complete. Total rows/entries processed: N`.

   After a successful run, remove the data files — they are now in PostgreSQL and are no longer needed on the VPS:

   ```bash
   rm -rf ~/data
   ```

   If the import fails, the `--dry-run` flag lets you inspect what would be loaded without writing to the database. Fix any issue (wrong path, bad env, unreachable postgres) and re-run; the upsert logic is idempotent for `ohlc_data`.

5. **Confirm the bot is healthy**:
   - `curl http://localhost:8000/health` from the VPS returns 200
   - `curl http://localhost:8000/status` shows `last_run_at` advancing across two consecutive calls 60 seconds apart
   - `/status` over Telegram returns the same data (proves the telegram service is reaching the bot)
6. **Delete the old script backup**: `rm ~/deploy_BoTC.sh.bak`.
7. **Move on to Phase 8 by committing directly to `main`.** The pipeline is now the gate; no feature branch needed unless the change is large enough to warrant review.

---

## Execution order (commits)

Each bullet is one focused commit. After each, run `docker compose -f docker-compose.test.yml build` locally to make sure nothing broke.

1. `feat(compose): add docker-compose.prod.yml override for image-based deploy`
2. `ci(workflows): add unified ci.yml with lint, unit, integration, build, and deploy`
3. `ci(workflows): remove old deploy.yml — superseded by unified ci.yml`
4. `docs(readme): add CI badges and document the unified CI/CD pipeline`

The PR can be opened after commit 4. The first deploy happens automatically on merge.

---

## Acceptance checklist

Run all of these before opening the PR:

- [ ] `docker-compose.prod.yml` exists, references `ghcr.io/jajiz/botc:${IMAGE_TAG:-main}` for both `botc` and `telegram`, and `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` parses cleanly.
- [ ] `.github/workflows/ci.yml` exists and defines five jobs in this exact `needs:` order: `lint`, `unit`, `integration` → `build-and-push` → `deploy`. Job display names are `Lint (ruff)`, `Unit tests`, `Integration tests`, `Build and push image`, `Deploy to VPS`.
- [ ] The `build-and-push` and `deploy` jobs have `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`.
- [ ] `.github/workflows/deploy.yml` is deleted.
- [ ] Every action reference uses a 40-character commit SHA followed by a `# vX.Y` comment. `grep -E "uses: [^@]+@v[0-9]" .github/workflows/` returns nothing.
- [ ] `README.md` has a working CI badge (URL: `actions/workflows/ci.yml/badge.svg?branch=main`) and a Python 3.12 badge.
- [ ] The Continuous integration and deployment section in `README.md` documents the five jobs, the GHCR tag scheme, and the rollback command.
- [ ] All Step 0 manual prerequisites (VPS layout, secrets, package visibility plan) are complete.
- [ ] On the PR, all three test jobs go green; `build-and-push` and `deploy` correctly do **not** run on the PR.
- [ ] After merging, the full pipeline runs, the GHCR package is created, and the VPS is running the new image. `curl :8000/status` returns a fresh `last_run_at`.

---

## Non-goals for this phase

Explicitly out of scope — do not add any of these:

- **Multi-environment deploys** (staging, prod). Single VPS, single environment.
- **Semver / release tagging.** Commit-SHA tags + a moving `:main` tag are sufficient. Adding `git tag`-driven semver is a future phase if it ever becomes useful.
- **`mypy` / `pyright` in CI.** Static type checking remains deferred (Phase 6 non-goal).
- **Live Kraken credentials in CI.** Those tests stay local-only.
- **Pre-commit hooks.** Orthogonal to the CI gate.
- **Automated VPS provisioning** (Ansible, Terraform). The VPS is a hand-managed pet; that is fine for V2.
- **Image vulnerability scanning** (Trivy, Snyk, GHCR's built-in scan UI). Worth doing later — not in this PR.
- **CodeQL / Dependabot / GitHub Advanced Security features.** Outside V2 scope.
- **Refactoring `scripts/entrypoint.sh`** so only the bot service runs migrations. Currently both services do; it's a no-op the second time. Leave as-is.
- **A `CHANGELOG.md` entry** (Phase 9 owns the changelog introduction).

---

## Appendix — keeping the GHCR package private

If you decide to keep `ghcr.io/jajiz/botc` private (Step 0.3 alternative), the VPS needs to authenticate before it can pull. Set this up once:

1. GitHub → your account → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token. Scope: **`read:packages`** only. Treat it as a long-lived secret.
2. On the VPS:
   ```bash
   echo '<the-token>' | docker login ghcr.io -u jAjiz --password-stdin
   ```
3. Verify: `docker pull ghcr.io/jajiz/botc:main` succeeds.

The credential is stored in `~/.docker/config.json` and persists across reboots. Rotate the PAT every 6–12 months.

The workflow itself does **not** need this token — `${{ secrets.GITHUB_TOKEN }}` is sufficient for the `push` direction.
