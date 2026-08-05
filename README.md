# `iblai/pr-e2e-action`

Runs the Playwright e2e suites for a pull request in **`iblai/os`**, **`iblai/lms`** and
**`iblai/iblai-web-frontend`**, by dispatching the central pipeline in `iblai/iblai-deploy-ops`,
streaming its progress back into the caller's log, and publishing the report link onto the PR.

## Why this is an action, and not a workflow in each repo

A `pull_request` event runs workflows from the PR's **merge ref**. A workflow copied into each repo
is therefore *frozen per PR*: a fix reaches no open PR until that PR rebases.

On 2026-08-04 that meant a merged fix reached **none of ~27 open PRs**, and every bug needed three
separate PRs (os + lms + web-frontend) plus a rebase on each open PR to take effect.

`uses: iblai/pr-e2e-action@v1` is resolved **at run time from this repo**, so a fix tagged `v1`
applies to every PR immediately — including PRs opened months ago, with no rebase.

## Usage

```yaml
  e2e:
    name: E2E (central pipeline)
    needs: build-app-image
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }        # needed to diff changed spec files
      - uses: iblai/pr-e2e-action@v1
        with:
          source_repo:    os            # os | lms | web-frontend
          pr_number:      ${{ github.event.pull_request.number }}
          merge_sha:      ${{ github.sha }}
          head_sha:       ${{ github.event.pull_request.head.sha }}
          base_sha:       ${{ github.event.pull_request.base.sha }}
          app_image:      ${{ needs.build-app-image.outputs.image-uri }}
          dispatch_token: ${{ secrets.DEPLOY_OPS_DISPATCH_TOKEN }}
          repo_token:     ${{ secrets.GITHUB_TOKEN }}
```

### Inputs

| input | required | default | notes |
|---|---|---|---|
| `source_repo` | yes | — | selects platform + suite in the central pipeline |
| `pr_number` | yes | — | |
| `merge_sha` | yes | — | `github.sha` on a `pull_request` **is** the merge commit; the app image and the suite must come from the same one |
| `head_sha` | yes | — | branch tip |
| `base_sha` | yes | — | used to diff changed specs |
| `app_image` | yes | — | SPA image built from `merge_sha` |
| `mode` | no | `auto` | `auto` \| `full` \| `selective` |
| `specs` | no | `''` | space-separated spec files; with `mode=selective` this wins over auto-detection |
| `dispatch_token` | yes | — | PAT with `Actions: write` on `iblai/iblai-deploy-ops` |
| `repo_token` | yes | — | `GITHUB_TOKEN`; only used to write the PR's sticky comment |

### Outputs

`conclusion` — the central run's conclusion · `run_id` — the dispatched run id

## Selective runs

Most failures are a single flaky spec, and re-running all 621 tests to re-check one is what makes
the shared-environment queue back up. Pass an explicit spec list to run just that file:

```yaml
mode: selective
specs: e2e/journeys/34-workflows.spec.ts
```

Roughly **3–5 minutes instead of ~45**. Auto-detection still applies when `mode: auto`: a PR that
touches only spec files (≤5) runs just those; anything touching app source runs the full suite.

## Versioning

- `v1.0.0`, `v1.1.0`, … are **immutable**
- `v1` is a **moving tag** — this is what callers pin, and what makes fixes propagate
- To roll back: re-point `v1` at the previous immutable tag

Callers pin `@v1` deliberately. That means a defect here affects all three repos at once — the
trade is that recovery is one commit and a tag move, rather than three PRs and a rebase per open PR.

## What this action does NOT contain

No secrets, and no deploy logic. It dispatches and reports; all deployment, environment mutation and
test execution stay in the private `iblai/iblai-deploy-ops`. Runner labels, environment names and
that repo's name were already public in os's workflow before this action existed.
