# `iblai/pr-e2e-action`

Runs the Playwright e2e suites for a pull request in **`iblai/os`**, **`iblai/lms`** and
**`iblai/iblai-web-frontend`**, by dispatching the central pipeline in `iblai/iblai-deploy-ops`,
streaming its progress back into the caller's log, and publishing the report link onto the PR.

## Reading your PR e2e result

Every run ends with a comment on your PR. Its title says what happened, and the bold sentence under
it says whose problem it is and what to do.

| The comment says | What it means | What to do |
|---|---|---|
| ✅ **PR E2E passed** | Every test passed against the release running in prod. | Nothing. |
| ❌ **PR E2E failed** | The tests ran and some failed. | Open **Full report, traces and screenshots**. Every failure has a screenshot and a trace; the trace's **Console** tab shows browser errors. `/retest <spec>` re-runs one file in minutes. |
| ❌ **PR E2E couldn't run your build** | Your build never got as far as the tests: it didn't start on the test server, or its e2e tests didn't build. | **Details** has the error and the last lines it printed. |
| ⚠️ **infrastructure problem, not your code** | The shared test environment failed, not your PR. | Nothing in your PR. Comment `/retest`; if it fails the same way, tell the ops team. |
| ⏹️ **PR E2E stopped** | The run was cancelled, usually because a newer commit or another `/retest` replaced it. | `/retest` if you still need a result. |
| ❓ **PR E2E failed**, cause unclear | The pipeline couldn't tell why. | **Details** has what it saw. `/retest`, and tell the ops team if it happens again. |

The same sentence is at the top of the *E2E* check's run page. In os and lms it is also the
**PR Validation** status in the merge box, linked to the comment. `/status` says what your run is
doing right now; `/report` links the latest report. The pipeline run linked under **Details** is
private and only for maintainers: everything you need is in the comment and the report.

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

## Which environment does it run on?

**This action does not choose, and deliberately cannot.** It sends no `target_env`; the central
`pr-e2e.yml` resolves `${{ vars.PR_E2E_ENV || inputs.target_env }}` — one variable in
`iblai/iblai-deploy-ops`, consulted by every consumer (`concurrency`, `environment`, the runner
lookup, the report path, `run-name`).

Earlier versions probed the runners here and picked stg1 or stg2. That gave the caller a *requested*
env that a central override could silently contradict, which was the precondition for both
2026-08-04 incidents — a live-lock and a bug that abandoned four healthy runs. With one source of
truth the two values cannot disagree, because there is only one.

Operators move all PR traffic in one command, with no PR and no rebase:

```sh
gh variable set PR_E2E_ENV --body stg2 -R iblai/iblai-deploy-ops
gh variable delete PR_E2E_ENV -R iblai/iblai-deploy-ops   # back to the stg1 default
```

## Slash commands — `iblai/pr-e2e-action/command@v1`

A second action in this repo handles PR comments, so nobody has to remember the label ritual:

| command | what it does |
|---|---|
| `/retest` | re-run the full suite |
| `/retest <spec>` | re-run only matching spec files — **minutes instead of ~45** |
| `/status` | queue position and what is running |
| `/report` | link to the latest report |
| `/cancel` | stop this PR's run and free the environment |

`<spec>` is a substring, so `/retest 34` and `/retest 34-workflows` both resolve to
`e2e/journeys/34-workflows.spec.ts`. Several are allowed. An unmatched token starts nothing and
says so, rather than silently running everything.

### Why it recycles the label instead of dispatching

The handler's only trigger action is remove-then-add of `run-tests`. It never calls the central
pipeline directly. Two reasons, and the second is the one that bites:

- there is a single trigger path to reason about and secure, not two;
- a PR's **required check** only updates when the caller workflow runs. A command that produced a
  report without refreshing the check would look like it worked while leaving the PR unmergeable.

### Security on a public repo

`issue_comment` workflows always run from the **default branch**, never from the PR — so a fork PR
cannot edit the handler to weaken its own checks. On top of that the action requires
**write/maintain/admin** via `repos/{repo}/collaborators/{user}/permission`, and refuses fork PRs.

Authorization deliberately does **not** use `author_association`: it reports `MEMBER` for any org
member, including people with no write access to the repo, who would then be able to occupy a
shared test environment.

### How `/retest <spec>` reaches the run

The handler writes a marker comment and recycles the label. The root action reads that marker at
the start of the run and honours it only if **both** hold:

- it was authored by `github-actions[bot]` — a user cannot post as the bot, so hand-writing the
  comment cannot bypass the permission check; and
- its `head_sha` matches the commit under test — otherwise a selective request made against an
  earlier commit would silently narrow a **later** push's run to a few files, leaving the rest of
  the suite untested while the required check went green.

Either check failing falls back to normal auto-detection. A malformed marker is ignored.

## Selective runs

Most failures are a single flaky spec, and re-running all 621 tests to re-check one is what makes
the shared-environment queue back up. Pass an explicit spec list to run just that file:

```yaml
mode: selective
specs: e2e/journeys/34-workflows.spec.ts
```

Roughly **3–5 minutes instead of ~45**. Auto-detection still applies when `mode: auto`: a PR that
touches only spec files (≤5) runs just those; anything touching app source runs the full suite.

## Cancellation cleanup lives in the CALLER, not here

A composite action is **terminated when the job is cancelled**, so an `if: cancelled()` step inside
this action would never run. Cleanup must therefore be a job step in the calling workflow:

```yaml
      - uses: iblai/pr-e2e-action@v1
        id: e2e
        with: { ... }

      - name: Cancel the dispatched central run if this caller is cancelled
        if: cancelled()
        env: { GH_TOKEN: "${{ secrets.DEPLOY_OPS_DISPATCH_TOKEN }}" }
        run: |
          F="$RUNNER_TEMP/pr-e2e-run-id"
          [ -s "$F" ] || { echo "nothing to cancel"; exit 0; }
          gh run cancel "$(cat "$F")" -R iblai/iblai-deploy-ops || true
```

This matters on the routine path, not an edge case: callers use `cancel-in-progress: true`, and a
label recycle fires two events, so the first caller is cancelled on essentially every re-tag.
Without this the dispatched central run orphans — holding an environment for a full suite, producing
a result nobody reads, and sometimes passing while the PR shows red.

**The run id comes from `$RUNNER_TEMP/pr-e2e-run-id`**, which this action writes the moment it
dispatches — not from `steps.e2e.outputs.run_id`. A composite action's declared outputs are only
published when it *completes*, and by definition it has not completed when cancelled.

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
