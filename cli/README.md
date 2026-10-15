# ephemera CLI

Deploy an [Ephemera](../README.md) preview from any CI/CD pipeline, after the pipeline has built its images: wait until it works, get its URL, remove it when done. The design is in [docs/ci-pipeline.md](../docs/ci-pipeline.md).

```bash
pip install ephemera-cli        # or: docker run --rm -e EPHEMERA_TOKEN ghcr.io/sabiut/ephemera-cli ...
export EPHEMERA_TOKEN=eph_...   # a pipeline token: dashboard → API Tokens, with the repository filled in

ephemera preview deploy --repo acme/shop --name pr-142 \
  --image web=ghcr.io/acme/shop@sha256:4f1c... \
  --wait --timeout 10m --output json

ephemera preview status --repo acme/shop --name pr-142
ephemera preview delete --repo acme/shop --name pr-142
```

## deploy

| Option | |
|---|---|
| `--repo owner/name` | The GitHub repository, with the Ephemera App installed. Detected from `GITHUB_REPOSITORY`, or set `EPHEMERA_REPOSITORY`. |
| `--name` | The preview's name: up to 20 characters, `a-z`, `0-9` and `-`. Deploying the same name again updates that preview and keeps its links. |
| `--commit` | The full SHA the images were built from. Detected in GitHub Actions (the pull request's head, not the merge commit), GitLab (`CI_COMMIT_SHA`) and Jenkins (`GIT_COMMIT`), or set `EPHEMERA_COMMIT`. |
| `--image service=image` | The image for a compose service. Repeat per service. Use `@sha256:` digests, so the preview runs exactly what this pipeline built. |
| `--compose FILE` | A compose file to use instead of the repository's at the commit. |
| `--ttl HOURS` | Removed after this long unless deleted (default 24, at most 168). |
| `--pull-request N` | Also removed when that pull request closes; Ephemera comments on it. |
| `--serial N` | A run number that only grows (for example `$GITHUB_RUN_NUMBER`). An older run is refused instead of overwriting a newer one. |
| `--wait`, `--timeout` | Wait until the preview is ready or failed (default 10 minutes). |
| `--output json` | Machine-readable result on stdout; progress always goes to stderr. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Ready (or, without `--wait`, accepted) |
| 1 | The preview failed: the output says why and what to do |
| 2 | Usage or configuration error (a missing token, option or value) |
| 3 | Timed out waiting |
| 4 | Ephemera refused the request (the reason is printed) or could not be reached |

`EPHEMERA_URL` points the CLI at another Ephemera server.

## Development

Standard library only. Tests: `pytest` in this directory. `api/tests/test_cli_contract.py` runs the CLI against the real API in process. Releases: tag `cli-vX.Y.Z` (after bumping `ephemera_cli/__version__`).
