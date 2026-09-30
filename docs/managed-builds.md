# Managed builds: design

Status: design agreed (decisions below). No code yet.

## Why

Today a user needs four things before a preview runs their own code: install the GitHub App, have a `docker-compose.yml`, add a CI workflow that builds and pushes an image for every commit (plus `image:` lines in compose), and give the cluster read access to those images (make packages public, or create and paste a registry token). The last two are the onboarding cost reviewers keep pointing at, and both exist only because Ephemera does not build images.

With managed builds the journey becomes: install the App, confirm what Ephemera detected, open a pull request. No workflow, no registry, no tokens, and private code stays private.

This is a **limited beta**. The existing path, where a service names a CI-built `image:`, stays exactly as it is and keeps working for repositories that prefer it or need things managed builds will not support at first.

## Goals and non-goals

Goals for the beta:

- Build each PR commit's images from the repository's Dockerfiles, privately, with no user configuration beyond confirming what was detected.
- Show build progress and build failures in the same places as deploy progress and failures today (PR comment and status, dashboard stages and details), with the same "what happened / what to do / the button that does it" standard.
- Bound cost and blast radius from day one: timeouts, concurrency, per-repository monthly limits, cancellation of superseded builds, image cleanup.
- Measure whether it actually removes pain: installation to first working preview, and how often people need help.

Not in the beta (explained to the user before a build, not discovered after one fails):

- Build secrets (private npm/pip registries, `RUN --mount=type=secret`, SSH forwarding). Those repositories keep using CI-built images.
- Building from forks without a maintainer's approval (see Security).
- Repositories without a `docker-compose.yml` (a Dockerfile alone). Compose remains the description of what a preview runs; Dockerfile-only support may follow the beta.
- Multi-platform images; non-Docker builds (Buildpacks, Nix); monorepo path filters.

## User experience

1. **Detection.** When a repository is connected (or on demand), Ephemera reads the default branch and proposes a build plan:
   - For each compose service with `build:` (context, dockerfile, target, args, as the setup guide already parses them), the service name, build context, Dockerfile path, the port from `ports:` / `EXPOSE`, and whether it is public.
   - Anything unsupported is listed with its reason (build secrets, `ssh:`, `dockerfile_inline`, a Dockerfile that references a private base image the build cannot pull).
2. **Confirmation.** The Repositories page shows the plan as a short table (service, context, Dockerfile, port, public) with **Enable managed builds**. The confirmed plan is stored per repository; changes to compose on later commits are re-detected per build and surfaced if they differ materially (new service, removed Dockerfile).
3. **A pull request.** The deploy progress gains a **Building** stage before Deploying services, per service: queued → building (with elapsed time) → pushed. The PR status says "Building web (1m 20s)". A newer push cancels the older build.
4. **Failures** join the existing diagnosis table, with actions:

   | Category | Explanation | Action |
   |---|---|---|
   | `build_dockerfile_missing` | The Dockerfile named for `web` isn't at this commit | Check this PR's configuration |
   | `build_step_failed` | Step `RUN npm ci` failed (last lines of the log shown) | View build log |
   | `build_dependency_denied` | A dependency download was refused (private registry) | Explain the CI-image alternative |
   | `build_timeout` | The build ran past 15 minutes | View build log; explain caching |
   | `build_limit` | This repository used its monthly build minutes | See usage; the date it resets |
   | `build_fork_pending` | Waiting for a maintainer to approve building a fork's PR | Approve build (collaborators only) |

5. **Logs.** Each build's log is stored by Ephemera (the tail inline in the details view, the full log downloadable), so users never need Google Cloud access.

## Architecture

```
PR webhook ──> build planner ──> build queue (per repository: 1 running, newest wins)
                                        │
                     worker: fetch commit tarball with the installation token
                              upload to gs://<builds-bucket>/<build-id>.tgz  (1-day lifecycle)
                                        │
                     Cloud Build (per-repository identity, 15-min timeout)
                       docker buildx build --cache-from/--cache-to <repo cache image>
                       push <region>-docker.pkg.dev/<project>/<repo-registry>/<service>:<sha>
                                        │
                     worker polls build status, stores log, records minutes
                                        │
                     deploy: build-only services use the built image (existing flow)
```

- **Source.** The worker downloads the commit's tarball through the GitHub App (Contents: read, which it already has) and uploads it to a builds bucket. The build itself never receives a GitHub credential.
- **Where images go.** Each connected repository with managed builds is assigned a **build slot**: an Artifact Registry repository and a build service account, created by Terraform (`modules/managed-builds`, `build_slots`, 10 for the beta). Ephemera records the assignment; a slot is wiped before it is reassigned. The GKE nodes' service account already reads every Artifact Registry repository in the project, so built images need no pull Secret.
- **Deploy integration.** `deploy_application` already interpolates `${EPHEMERA_SHA}` and knows which services are build-only. With managed builds enabled, a build-only service's image becomes the built image for that commit, and the "Nothing to preview / No image to run" blockers no longer apply to it. Services that name their own `image:` are untouched.
- **Queue.** A `builds` table (repository, PR, commit, service, status, Cloud Build id, started/finished, minutes, log object, failure category). The environment lock already serialises work per preview; a per-repository build lock bounds concurrency there, and a global cap bounds the platform.

## Security

Managed builds run strangers' code with an identity that can push images. The design assumes a malicious `Dockerfile`.

- **Per-repository build identity.** Each repository builds as its slot's service account, which can only push to **its own** registry, read **its own** prefix of the source bucket and write **its own** prefix of the logs bucket (IAM conditions): no project roles, no access to other repositories' images, caches or source, no Secret Manager, no GKE. A build step can reach the metadata server and obtain that account's token; per-repository accounts are what make that token useless against other customers.
- **No other credentials in the build.** The source arrives as a tarball; the build has no GitHub token, no registry token, no Ephemera secrets. Build arguments come only from the confirmed plan.
- **Isolation of caches and artifacts.** Layer caches live in the repository's own registry. Source tarballs are per build, with a one-day lifecycle. Nothing is shared between repositories.
- **Forks.** A PR from a fork is not built automatically: fork code could overwrite the repository's own commit images (its identity can push to that registry). A collaborator clicks **Approve build** for that PR's current commit; a new push needs approval again. Previews of same-repository branches build automatically.
- **Least privilege for Ephemera itself.** The worker (its own Kubernetes service account, bound by Workload Identity to `ephemera-builds-controller`) may create and cancel builds, act as **only the slot accounts**, manage only the slot registries, and use the two build buckets. It holds no IAM-admin role, so it cannot create identities or grant itself anything; that is why slots are created by Terraform rather than on demand.
- **Supply chain.** Base images are pulled as written; the beta does not pin or scan them. Built images carry labels for repository, commit and build id.

## Limits and cleanup (required before the beta opens)

| Control | Beta default | Enforced by |
|---|---|---|
| Build timeout | 15 minutes | Cloud Build `timeout` |
| Concurrent builds | 1 per repository, 4 platform-wide | build queue |
| Superseded builds | cancelled when a newer commit arrives | Cloud Build cancel API |
| Monthly build minutes | 300 per repository (configurable) | recorded minutes; `build_limit` failure |
| Machine type | `e2-standard-2`, the default pool | build request |
| Images | PR tags deleted when the PR closes or expires; untagged images removed after 1 day | cleanup task plus registry cleanup policy |
| Source tarballs | deleted after 1 day | bucket lifecycle |

## Cost

Cloud Build lists **$0.006 per build-minute** for `e2-standard-2`, with **2,500 promotional free minutes per billing account** for that machine type in the default pool ([pricing](https://cloud.google.com/build/pricing)). As examples, not a forecast: 4,000 eligible minutes a month is about $9 of compute and 40,000 about $225, before storage and network. Multi-service repositories, retries, cache misses and image storage all raise the total. The controls above bound it per repository; the metrics below show where it goes.

## Measuring success

Recorded as events, reported on an internal page:

- Installation → first **verified** Ready preview (median and 90th percentile), split by managed builds versus CI images.
- Share of first previews that needed intervention: a failed setup check, a failed build, a retry, a recovery button, a support contact.
- Build outcomes by category, build minutes per repository, cache hit rate, time spent queued.

The beta succeeds if most new users reach a working preview without help and faster than the CI-image path, within the cost bounds.

## Rollout

1. **Infrastructure** (Terraform, `modules/managed-builds`): Cloud Build API; source (1-day) and logs (30-day) buckets; the controller identity for the worker; `build_slots` slots, each a service account plus a registry with a cleanup policy and conditional bucket access.
2. **Detection and confirmation**: build plan API and the Repositories page table; nothing builds yet.
3. **Build pipeline** behind an allowlist of repositories: queue, source upload, Cloud Build, status polling, logs, deploy integration.
4. **Limits**: cancellation, per-repository and platform caps, monthly minutes, image cleanup, fork approval.
5. **Experience**: Building stage, PR status and comment, build diagnoses with actions, log view; extend the end-to-end test with a managed-build variant.
6. **Beta**: open to new installations, with the metrics page.

Each step is one or more PRs; this is several weeks of work, not a single change.

## Decisions

Agreed with the owner on 2026-10-01:

1. **Monthly build minutes:** 300 per repository per month during the beta.
2. **Compose stays required.** Managed builds build the `build:` services of the repository's `docker-compose.yml`; repositories with only a Dockerfile are not part of the beta.
3. **Forks:** a collaborator approves each commit of a fork's pull request (**Approve build**); a new push needs approval again.
4. **Region:** builds run in `us-central1`, next to the cluster and its registry.
