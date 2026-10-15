# Ephemera preview: GitHub Action

Deploy a working preview of your app from the images this workflow built, wait until it works, run your tests against it, and have it removed when the job ends, even if the tests fail. Design: [docs/ci-pipeline.md](../../docs/ci-pipeline.md).

```yaml
name: Preview
on: pull_request

jobs:
  preview:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with: { registry: ghcr.io, username: "${{ github.actor }}", password: "${{ secrets.GITHUB_TOKEN }}" }
      - id: build
        uses: docker/build-push-action@v6
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.event.pull_request.head.sha }}

      - id: preview
        uses: sabiut/ephemera/actions/preview@main
        with:
          token: ${{ secrets.EPHEMERA_TOKEN }}          # a pipeline token for this repository
          name: pr-${{ github.event.number }}
          images: |
            web=ghcr.io/${{ github.repository }}@${{ steps.build.outputs.digest }}
          serial: ${{ github.run_number }}

      - run: npx playwright test
        env:
          BASE_URL: ${{ steps.preview.outputs.url }}
          # Only needed if the repository's previews are protected:
          EPHEMERA_ACCESS: ${{ steps.preview.outputs.access-token }}
```

In `playwright.config.ts`, send it with every request:

```ts
use: { baseURL: process.env.BASE_URL, extraHTTPHeaders: { 'X-Ephemera-Access': process.env.EPHEMERA_ACCESS ?? '' } }
```

The preview is removed when the job ends. Set `keep: true` to leave it running for reviewers instead; it is then removed when the pull request closes, or after `ttl-hours`.

## Inputs

| Input | Default | |
|---|---|---|
| `token` | required | A pipeline token from the Ephemera dashboard (API Tokens, with the repository filled in), stored as a secret. |
| `name` | required | The preview's name, such as `pr-142`. Deploying the same name again updates it and keeps its links. |
| `images` | | One per line, `service=image`, for the compose services this workflow built. Prefer `image@sha256:` digests. |
| `repository` | this repository | `owner/name`. |
| `commit` | the PR's head commit, else `GITHUB_SHA` | The commit the images were built from. |
| `compose` | the repository's | A compose file to use instead. |
| `ttl-hours` | `24` | Removed after this long if the job never removes it (1-168). |
| `pull-request` | the triggering PR | Ephemera comments on it, and removes the preview when it closes. |
| `serial` | | A run number that only grows, such as `github.run_number`. An older run is refused instead of overwriting a newer one. |
| `wait` | `true` | Wait until ready. The step fails, with Ephemera's explanation, if the preview fails or times out. |
| `timeout` | `10m` | How long to wait. |
| `keep` | `false` | Keep the preview when the job ends. |
| `access-minutes` | `30` | How long the `access-header` output works (1-60). |
| `ephemera-url` | `https://ephemera-api.devpreview.app` | The Ephemera server. |

## Outputs

`url` (the main link), `urls` (JSON, every public service), `id`, `status`, `access-header` and `access-token` (for tests against a protected preview, masked in the log), and `exit-code` (the CLI's: 0 ready, 1 failed, 2 usage, 3 timed out, 4 refused or unreachable).

## How it works

The action runs the [ephemera CLI](../../cli/README.md) from this repository, at the same version, with the runner's Python. Nothing is installed. Its post step removes only the preview this run deployed: a run whose deploy was refused, such as an older run, never removes a newer run's preview. If removal fails, the job still passes, and the preview's expiry removes it later. Pull requests from forks get no secrets, so the step explains that instead of failing obscurely.
