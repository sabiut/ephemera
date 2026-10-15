# Previews from a CI/CD pipeline: design

Status: design agreed (decisions below). Steps 1 to 4 are merged: the model and API, the CLI (`cli/`), the GitHub Action (`actions/preview/`) and test access to protected previews. The pipeline end-to-end test (step 5) is next.

## Why

Today a preview comes from a pull request: the GitHub App sees it, and Ephemera fetches the compose file and either runs the images it names or builds them (managed builds). That suits small teams.

Companies with an established pipeline want a different shape: a **"Deploy preview" stage** in the pipeline they already run, after they have built and pushed their images, before the tests that need a running application:

```
build images → deploy Ephemera preview → wait until ready → run tests against the URL → share the URL → clean up
```

They keep their builds, registries and test stages; Ephemera adds the running environment, the link, access control and guaranteed cleanup. This is the promise the product review proposed: *add working previews to the pipeline you already have*.

## What it looks like for the user

A step in any CI system (the GitHub Action and GitLab template are thin wrappers around the same CLI):

```bash
ephemera preview deploy \
  --repo acme/shop \
  --name pr-142 \
  --commit "$GIT_SHA" \
  --image web=ghcr.io/acme/shop@sha256:4f1c… \
  --image worker=ghcr.io/acme/shop-worker@sha256:9ab2… \
  --wait --timeout 10m \
  --output json
```

```json
{"id": 812, "name": "pr-142", "status": "ready", "commit": "3f9e2c1",
 "url": "https://ci-pr-142-shop-f70306-web.devpreview.app",
 "urls": {"web": "https://ci-pr-142-shop-f70306-web.devpreview.app"},
 "expires_at": "2026-10-16T12:00:00Z"}
```

- The command exits 0 when the preview is Ready, and non-zero with the diagnosis (the same explanations the dashboard gives: "Image is private", "web keeps crashing, exit code 1", …) when it fails or times out. Progress is printed to stderr so the JSON on stdout stays clean.
- Running the same command again with the same `--name` **updates** that preview (same links), as a push to a PR does today.
- `ephemera preview delete --repo acme/shop --name pr-142` removes it; so does its expiry if the pipeline never gets there (cancelled, crashed).
- `ephemera preview access-header --repo … --name …` prints a short-lived header (15 minutes by default, at most 60) that lets the pipeline's tests reach a protected preview without making it public.

GitHub Actions:

```yaml
- uses: sabiut/ephemera-preview@v1
  id: preview
  with:
    token: ${{ secrets.EPHEMERA_TOKEN }}        # or OIDC, see Authentication
    name: pr-${{ github.event.number }}
    images: |
      web=${{ steps.build.outputs.web-digest }}
- run: npx playwright test
  env:
    BASE_URL: ${{ steps.preview.outputs.url }}
```

## Model

A **pipeline preview** is an environment identified by **(repository, name)** instead of (repository, pull request). Everything after creation is the existing machinery: the namespace with its quota, isolation and network policies; registry tokens; protection; readiness checks; diagnosis; the dashboard; cleanup.

| | Pull-request preview (today) | Pipeline preview (new) |
|---|---|---|
| Identity | repository + PR number | repository + name (`[a-z0-9-]`, ≤ 20 chars) |
| Created by | GitHub webhook (or API) | `POST /api/v1/previews` from the pipeline |
| Compose file | repository, at the PR's commit | repository at `commit` (default), or sent in the request |
| Images | from compose, or managed builds | from compose, with `images` overriding per service; never built by Ephemera |
| Updated by | a push to the PR | the same request again |
| Removed | PR closed; idle 7 days; Stop | `DELETE`; its expiry (default 24 h, max 7 days); optionally its PR closing |
| Status on GitHub | commit status + PR comment | commit status if a commit is given; a PR comment only with `pull_request` |
| Namespace | `pr-142-shop-f70306` | `ci-pr-142-shop-f70306` (`ci-` prefix: never collides with a PR preview) |

Data model (additive migration, like every other): `environments` gains `source` (`pull_request` | `pipeline`), `name`, `expires_at`, and `image_overrides`; `pr_number` becomes nullable; a unique constraint on (repository, name) for pipeline previews alongside the existing one for PRs.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/previews` | Create or update. Body: `repository`, `name`, `commit` (optional), `images` `{service: ref}`, `compose` (optional text), `ttl_hours`, `pull_request` (optional: link to a PR, removed when it closes), `serial` (optional, see below). Returns 202 with the preview. |
| `GET` | `/api/v1/previews/{id}` or `?repository=&name=` | Status, stage, URLs, readiness, diagnosis, `expires_at`. The CLI's `--wait` polls this. |
| `DELETE` | `/api/v1/previews/{id}` | Remove now. |
| `POST` | `/api/v1/previews/{id}/access` | A short-lived (15 min) signed header for tests to reach a protected preview. |

**Ordering.** Pipelines run concurrently and out of order; an older run must not overwrite a newer one. The existing per-preview lock and "superseded" check already make the **latest request** win while one is in flight. Pipelines that need strict ordering pass `serial` (a run number that only grows): a request with a lower `serial` than the preview's last is refused with 409 and the CLI exits non-zero with "a newer run (serial 58) already deployed this preview".

**Images.** Overrides should be immutable references (`@sha256:` digests); tags are accepted, with a warning, because a moving tag makes "which code is this?" unanswerable. Every image passes the same rules as today: hardened pods, no images from Ephemera's own registries (#75), private registries through the repository's read-only tokens.

## Authentication

1. **v1: API tokens**, as today, but **scoped**: a pipeline token is limited to one repository and to previews (create, read, delete, access header), with an expiry. Created on the repository's page in the dashboard; the existing broad API tokens keep working.
2. **v2: OIDC**, so pipelines hold no long-lived secret. GitHub Actions and GitLab CI issue a signed identity token per job; Ephemera verifies it against the provider's published keys and checks the `repository` (GitHub) or `project_path` (GitLab) claim against the repository being deployed. The CLI exchanges it for a 15-minute token. The repository's page lists which providers may deploy.

Who may deploy: the token's owner must be a collaborator on the repository (checked at creation, like today's API path); OIDC tokens are trusted for exactly the repository in their claim.

## Cleanup guarantees

- `DELETE` when the pipeline finishes (the Action does it in a post step, even when tests fail, unless `keep: true`).
- **Expiry** (`ttl_hours`, default 24, max 168) for the pipelines that never get there. The hourly job that expires idle PR previews removes expired pipeline previews too.
- A preview linked to a pull request is also removed when that PR closes.
- Pipeline previews count against the repository's preview limit (5 by default): a pipeline that leaks previews hits the limit and gets a clear error, not an ever-growing bill.

## Scope of the first version

**In**: GitHub-hosted repositories with the Ephemera App installed, deployed from **any** CI system (GitHub Actions, GitLab CI, Jenkins, CircleCI, Buildkite, …) — the repository is what Ephemera authorises against, not where the pipeline runs. The CLI (a single-file Python tool on PyPI, and a container image for CI systems without Python), the GitHub Action, a GitLab CI example, scoped tokens, `--wait`, delete, expiry, the access header, dashboard support (pipeline previews listed with their name and source).

**Later**: OIDC (v2, above); repositories not on GitHub (needs workspaces independent of the GitHub App); building in the pipeline step (`--build`, reusing managed builds); per-preview resource sizes.

## Rollout

1. **Model and API**: migration, `POST/GET/DELETE /api/v1/previews`, image overrides, `serial`, expiry, scoped tokens. Tests, then a live run with `curl`.
   Until the CLI exists, a pipeline calls the API directly. Create a pipeline token first: `POST /api/v1/tokens/` with `{"name": "ci", "repository": "acme/shop"}`, or the dashboard's token form. It works only for the previews API, and only for that repository.

   ```bash
   # Deploy (or update) the preview; returns at once with its id
   curl -sf -X POST https://ephemera-api.devpreview.app/api/v1/previews \
     -H "Authorization: Bearer $EPHEMERA_TOKEN" -H "Content-Type: application/json" \
     -d '{"repository": "acme/shop", "name": "pr-142", "commit": "'"$GIT_SHA"'",
          "images": {"web": "ghcr.io/acme/shop@sha256:…"}, "pull_request": 142, "serial": '"$RUN_NUMBER"'}'

   # Poll until "ready" (use .url) or "failed" (read .diagnosis)
   curl -sf -H "Authorization: Bearer $EPHEMERA_TOKEN" https://ephemera-api.devpreview.app/api/v1/previews/812

   # Remove it when the pipeline is done
   curl -sf -X DELETE -H "Authorization: Bearer $EPHEMERA_TOKEN" https://ephemera-api.devpreview.app/api/v1/previews/812
   ```

   Responses:
   - `202`: accepted, with `action` (`created`, `updated` or `reprovisioned`) and any `warnings`, such as an image given by tag rather than digest.
   - `422`: something in the request can't work (name, commit, image reference, ttl).
   - `409`: an older `serial` than the run that last deployed this preview.
   - `429`: the repository's previews limit, which pull-request and pipeline previews share.
   - `403`: a pipeline token used for another repository, or for anything but `/api/v1/previews`.

   As built:
   - The images are applied with the compose converter (never the AI planner) and the rules from #75.
   - Images sent for services the compose file lacks produce a warning on the preview.
   - Commit statuses go under `ephemera/preview/<name>`, so they never overwrite a pull-request preview's on the same commit.
   - A PR comment is posted only when `pull_request` is given.
   - Closing that PR removes the preview.
   - The hourly expiry job removes it at its deadline.
   - The 15-minute transient-failure retry leaves pipeline previews to their pipeline.
   - The dashboard lists them as "pipeline: <name>", with no Retry button (the pipeline redeploys).
2. **CLI**: `deploy --wait`, `status`, `delete`, `access-header`; JSON output; exit codes; published to PyPI and as a container image.
   - `cli/`, package `ephemera-cli`, command `ephemera`. Standard library only (Python 3.9 or newer).
   - Commands: `preview deploy [--wait]`, `preview status` and `preview delete [--wait]` (see [cli/README.md](../cli/README.md)). `access-header` arrives with step 4, which adds its endpoint.
   - Exit codes: 0 ready, 1 failed (with the diagnosis), 2 usage, 3 timeout, 4 the API refused or was unreachable.
   - Detects the repository and commit in GitHub Actions (the PR's head, not the merge commit), GitLab CI and Jenkins.
   - `.github/workflows/cli.yml` tests on Python 3.9 and 3.12 and builds the package and image on every change. A `cli-vX.Y.Z` tag publishes to PyPI and `ghcr.io/sabiut/ephemera-cli`.
   - Publishing needs, once: a PyPI trusted publisher for this repository and the `cli.yml` workflow, with environment `pypi`.
3. **GitHub Action** with a post-step delete; GitLab CI and Jenkins examples in the docs.
   - `uses: sabiut/ephemera/actions/preview@main` ([actions/preview/README.md](../actions/preview/README.md)): deploy and wait, outputs `url`, `urls`, `id` and `status`, and a failed preview as an error annotation with the explanation.
   - Its post step (`post-if: always()`) removes the preview this run deployed, by id, so a refused older run never removes a newer run's preview. A failed removal is only a warning, since the expiry covers it. `keep: true` leaves the preview for reviewers.
   - It is a Node action without dependencies that runs this repository's CLI at the same ref. Composite actions can't have post steps.
   - It lives in this repository for now. The Marketplace needs `action.yml` at a repository's root, so listing it there means moving it to its own repository (`sabiut/ephemera-preview`).

   **GitLab CI** (any repository on GitHub with the App installed; the pipeline itself can run anywhere):

   ```yaml
   preview:
     image: ghcr.io/sabiut/ephemera-cli:latest      # or: pip install ephemera-cli
     variables:
       EPHEMERA_REPOSITORY: acme/shop                # the GitHub repository
     script:
       - ephemera preview deploy --name "mr-$CI_MERGE_REQUEST_IID" --commit "$CI_COMMIT_SHA"
           --image "web=$CI_REGISTRY_IMAGE@$WEB_DIGEST" --serial "$CI_PIPELINE_IID" --wait --output json > preview.json
       - export BASE_URL=$(python3 -c "import json; print(json.load(open('preview.json'))['url'])")
       - ./run-e2e-tests.sh
     after_script:
       - ephemera preview delete --repo acme/shop --name "mr-$CI_MERGE_REQUEST_IID"
   ```

   **Jenkins** (declarative):

   ```groovy
   stage('Preview') {
     environment { EPHEMERA_TOKEN = credentials('ephemera-token') }
     steps {
       sh 'pip install --user ephemera-cli'
       sh '~/.local/bin/ephemera preview deploy --repo acme/shop --name "build-$BUILD_NUMBER" --commit "$GIT_COMMIT" --image "web=$IMAGE@$DIGEST" --wait'
     }
     post { always { sh '~/.local/bin/ephemera preview delete --repo acme/shop --name "build-$BUILD_NUMBER" || true' } }
   }
   ```
4. **Protected previews for tests**: the access header accepted by the sign-in check.
   - `POST /api/v1/previews/{id}/access` (`{"minutes": 1-60}`, default 15) returns `X-Ephemera-Access` and a signed value for that preview's namespace and the caller.
   - The sign-in check (`/preview-auth/check`, which nginx asks before each request) accepts it like the cookie. The caller must still be able to view the preview at every request, and the value works for no other preview and for nothing else (it isn't a cookie value).
   - CLI: `ephemera preview access-header --repo … --name … [--minutes N] [--format header|value|json]`.
   - Action: outputs `access-header` and `access-token`, issued for `access-minutes` (default 30) once the preview is ready, and **masked** in the job log before they could appear.
   - Use with Playwright: `extraHTTPHeaders: {'X-Ephemera-Access': process.env.EPHEMERA_ACCESS}`; with curl: `-H "$EPHEMERA_ACCESS_HEADER"`.
5. **End-to-end**: a pipeline variant of the e2e test (build the test app's image in Actions, deploy it by digest, run a request against the URL, delete).
   - `.github/workflows/e2e-pipeline.yml` (run by hand: Actions → "End-to-end pipeline preview").
     - It checks out the test app and builds its image with a marker unique to the run (`GIT_SHA=e2e-<run>-1`, which the app's page prints).
     - It pushes the image to `ghcr.io/<owner>/ephemera-e2e-pipeline`, deploys it by digest **with the Action**, and checks the page shows the marker. That proves the preview runs exactly the image this pipeline built.
     - It builds image 2 and deploys under the same name with the CLI, then checks **the same link** now serves it.
     - It checks an older `--serial` is refused (exit 4, "newer run").
     - With `protected`, it also checks that an anonymous visitor is sent to sign in while the Action's masked `access-header` gets through. Protection is switched off again afterwards, even on failure.
     - A second job confirms the Action's post step **removed** the preview when the job ended.
   - It needs the secret `E2E_PIPELINE_TOKEN`: a pipeline token for the test repository. The protected variant also uses `EPHEMERA_TOKEN`.
   - The GHCR package starts private. Make it public once, or the first run fails with "Image is private", which is itself a check of that diagnosis.
6. **OIDC** (v2).

## Decisions

Agreed with the owner on 2026-10-15:

1. **Repositories hosted on GitHub only in v1**, with the Ephemera App installed; deployed from **any** CI system. Code hosted elsewhere waits for workspaces that don't depend on the GitHub App.
2. **Expiry:** 24 hours by default, 7 days at most.
3. **CLI in Python**, published to PyPI and as a container image for CI systems without Python.
4. **Pipeline previews count against the same per-repository limit** as pull-request previews (5 by default).
