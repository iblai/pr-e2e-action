#!/usr/bin/env python3
"""Offline tests for render.py: what a developer reads when their PR e2e run ends.

    python3 tests/test-render.py

Worth having because the comment is the only thing most developers see. On 2026-10-30 os#617's
comment said "another PR's run took the shared environment … says nothing about your code" for a
run that died on an ECR login failure, and nothing on the PR said what had happened.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("render", os.path.join(HERE, "..", "render.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

failures: list[str] = []


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label:<70} -> {str(got)[:90]}")
    else:
        print(f"  FAIL  {label:<70} -> {got}  (wanted {want})")
        failures.append(label)


def show(result, dispatch="failure", job_status="", evictions=0, **ctx):
    view = m.normalize(result, dispatch, job_status, evictions)
    return m.render(view, {"run_id": "41056935570", "head_sha": "f9bb8e0b9f1c", **ctx})


BASE = {"platform": "mentor", "mode": "full", "target_env": "stg1", "baseline": "1.340.0", "sync": "skipped"}
ECR = {**BASE, "schema": 2, "verdict": "skipped", "outcome": "infra", "owner": "infra", "pattern": "ecr_network",
       "stage_label": "installing your build on the test server",
       "headline": "Not your code: the test server lost network access to AWS, so it couldn't install your build.",
       "advice": "Nothing to change in your PR. Comment `/retest` to try again; if it fails the same way, tell the ops team.",
       "reason": "could not authenticate to 765174860755.dkr.ecr.us-east-1.amazonaws.com — failed on <host>: Connect timeout",
       "details": ["step failed: Swap the SPA to the PR build"], "env_warning": "Putting the shared test server back also failed.",
       "report_url": "", "tests": {}}

body, head = show(ECR, prev_report="https://tests.iblai.app/runs/pr-os-617-40945683733/mentor/index.html")
lines = body.splitlines()
check("an infra failure says so in the title", lines[1], "### ⚠️ PR E2E: infrastructure problem, not your code")
check("...the plain headline comes right after it", lines[3], f"**{ECR['headline']}**")
check("...then what to do", lines[5], ECR["advice"])
check("...it is a NEW comment, so the developer is notified", lines[0], "<!-- pr-e2e-report -->")
check("...it never claims another PR took the environment", "another PR" in body, False)
check("...the error sits in Details, in code", "- **Error:** `could not authenticate" in body, True)
check("...the restore warning is shown", "> ⚠️ Putting the shared test server back also failed." in body, True)
check("...the last real report is still linked",
      "[report](https://tests.iblai.app/runs/pr-os-617-40945683733/mentor/index.html)" in body, True)
check("...the private pipeline run is only offered to maintainers",
      "Pipeline run, for maintainers: [41056935570]" in body and body.index("Pipeline run") > body.index("<details>"), True)
check("...and the headline record carries it to the run page and merge box",
      (head["outcome"], head["owner"], head["headline"]), ("infra", "infra", ECR["headline"]))

FAILED = {**BASE, "schema": 2, "verdict": "failure", "gate": "FAIL", "outcome": "failed", "owner": "pr",
          "pattern": "tests_failed", "headline": "12 tests failed against prod 1.340.0. 2 more failures are already known and not yours.",
          "advice": "Open the report: every failure has a screenshot and a trace.",
          "report_url": "https://tests.iblai.app/runs/pr-os-617-41060344848/mentor/index.html",
          "tests": {"passed": 790, "failed": 14, "new": 12, "known": 2, "flaky": 0, "skipped": 220}}
body, head = show(FAILED)
check("failed tests: a failure title", body.splitlines()[1], "### ❌ PR E2E failed")
check("...the report link keeps the label earlier comments are searched by",
      f"**[Full report, traces and screenshots]({FAILED['report_url']})**" in body, True)
check("...the counts read as a sentence", "790 passed · 14 failed (12 new, 2 known) · 220 skipped" in body, True)
check("...no stale report is offered next to a fresh one", "Last completed run" in show(FAILED, prev_report="https://x")[0], False)

body, head = show({**BASE, "schema": 2, "verdict": "success", "outcome": "passed", "owner": "none",
                   "headline": "All 798 tests passed against prod 1.340.0.", "advice": "",
                   "report_url": "https://tests.iblai.app/r/index.html", "sync": "success"})
check("a pass says it passed", (body.splitlines()[1], head["outcome"]), ("### ✅ PR E2E passed", "passed"))
check("...and says the env was synced only when it was", "tested against prod `1.340.0` (env synced to it first)" in body, True)
check("a skipped sync is not described as a sync", "synced" in show(FAILED)[0], False)

body, _ = show({**ECR, "outcome": "failed", "owner": "pr", "pattern": "spa_not_answering",
                "headline": "Your build didn't start on the test server: it never answered on its port."})
check("a PR build that never starts is the developer's, not 'failed tests'",
      body.splitlines()[1], "### ❌ PR E2E couldn't run your build")

body, head = show({**ECR, "outcome": "cancelled", "owner": "none", "headline": "This run was stopped before it finished."})
check("a stopped run updates the single sticky notice", (body.splitlines()[0], head["marker"]), ("<!-- pr-e2e-norun -->",) * 2)

# ---- what really broke: the causes table ----------------------------------------------------------
CRASH = "TypeError: Cannot read properties of undefined (reading 'find')"
def cause(kind, count, text, known=False, snippet=None):
    return {"kind": kind, "plain": {"crash": "The app crashed and showed its error page"}.get(kind, "The test's check failed"),
            "text": text, "where": "31836-4fad.js:1:88062" if kind == "crash" else None, "snippet": snippet,
            "count": count, "consistent": True, "known": known,
            "example": {"title": "04-user-profile.spec.ts › J04 › opens the profile modal", "file": "04.spec.ts",
                        "trace_url": "https://trace.playwright.dev/?trace=x", "screenshot_url": "https://tests.iblai.app/s.png"}}

CRASHED = {**FAILED, "headline": f"Your build crashes in 37 of 61 failing tests: {CRASH}",
           "causes": [cause("crash", 37, CRASH, snippet="…M?.social_links.find(e=>e.platform)…"),
                      cause("symptom", 13, "Error: expect(locator).toBeVisible() failed | Locator: getByRole('button')"),
                      cause("symptom", 4, "J55 env not provisioned", known=True)]}
DEPS = m.deps_from_patch('@@ -40 +40 @@\n-    "@iblai/iblai-js": "2.50.1",\n+    "@iblai/iblai-js": "2.58.1",\n     "next": "15.5.28",')
body, _ = show(CRASHED, deps=DEPS)
check("the causes table leads with the crash, its count and where it happened",
      f"| 37 | App crash: `{CRASH}` at `31836-4fad.js:1:88062` | J04 › opens the profile modal" in body
      or f"| 37 | App crash: `{CRASH}` at `31836-4fad.js:1:88062` | opens the profile modal" in body, True)
check("...with the example's screenshot and trace",
      "[screenshot](https://tests.iblai.app/s.png) · [trace](https://trace.playwright.dev/?trace=x)" in body, True)
check("...a pipe in the error cannot break the table", "toBeVisible() failed \\| Locator" in body, True)
check("...the code at the crash is quoted", "…M?.social_links.find(e=>e.platform)…" in body, True)
check("...and the SDK bump in this PR is pointed at",
      "**Hint:** this PR changes `@iblai/iblai-js` `2.50.1` → `2.58.1`." in body, True)
check("known failures are set apart as probably not from the PR",
      "Probably not from your PR (known issues): 4 × The test's check failed" in body
      and "| 4 |" not in body, True)
check("a crash among failed tests is still titled 'failed', not 'couldn't run your build'",
      show({**CRASHED, "pattern": "tests_failed_crash"})[0].splitlines()[1], "### ❌ PR E2E failed")
check("the hint needs a dependency change", "**Hint:**" in show(CRASHED)[0], False)
check("...and a crash or browser error to point it at",
      "**Hint:**" in show({**CRASHED, "causes": [cause("symptom", 30, "x")]}, deps=DEPS)[0], False)
check("dependency diffs: only changed versions count",
      m.deps_from_patch('-  "a": "1",\n+  "a": "2",\n+  "b": "1",\n-  "c": "3",\n--- a/package.json\n+++ b/package.json'),
      [("a", "1", "2")])

# ---- older central runs (v1 fields only) and runs that left nothing --------------------------
v1 = {**BASE, "verdict": "failure", "gate": "FAIL", "failed": "3", "new_failures": "3", "report_url": "https://t/r"}
check("a v1 failure still reads as failed tests", show(v1)[1]["headline"], "3 tests failed against prod 1.340.0.")
check("a v1 pass still reads as a pass", show({**BASE, "verdict": "success"}, "success")[1]["outcome"], "passed")
body, head = show({**BASE, "verdict": "skipped", "report_url": ""})
check("a v1 run that died early says only what is known",
      (head["outcome"], head["headline"]), ("unknown", "The run stopped before the tests ran."))
check("...and does not guess that another PR took the environment", "another PR" in body, False)
body, _ = show({**BASE, "verdict": "skipped"}, evictions=2)
check("...unless this caller saw its run pushed out of the queue",
      "pushed out of the queue 2 times by other PRs' runs" in body, True)

for state, outcome in [("no_slot", "infra"), ("runner_unavailable", "infra"), ("dispatch_failed", "infra"), ("timed_out", "unknown")]:
    check(f"no central result, caller saw {state}", show({}, state)[1]["outcome"], outcome)
check("no central result and the caller job was cancelled: stopped", show({}, "", "cancelled")[1]["outcome"], "cancelled")
check("no central result after a failed run: says it can't be read",
      show({}, "failure")[1]["headline"], "The run failed, and its result couldn't be read.")

# ---- text from the pipeline cannot do anything on a public PR -----------------------------------
body, head = show({**ECR, "headline": "Not your code: ping @octocat about #123", "reason": "a `b` c",
                   "details": ["```", "x"]})
check("a pipeline headline cannot mention anyone", "@octocat" in body or "@octocat" in head["headline"], False)
check("...or link an issue", "#123" in body, False)
check("a reason with backticks stays one code span", "- **Error:** ``a `b` c``" in body, True)
check("details with a fence inside cannot close the block", "````text" in body, True)
check("a log link is labelled as a log",
      "**[Test log](https://tests.iblai.app/runs/x/mentor/logs/test-run.log)**" in show({**ECR, "report_url": "https://tests.iblai.app/runs/x/mentor/logs/test-run.log"})[0], True)
check("the headline record fits the merge box", len(show({**ECR, "headline": "x" * 300})[1]["headline"]) <= 140, True)

with tempfile.TemporaryDirectory() as tmp:
    os.environ.update(DISPATCH_CONCLUSION="no_slot", RUN_ID="", HEAD_SHA="abc", EVICTIONS="0")
    with contextlib.redirect_stdout(io.StringIO()):
        rc = m.main(["--result", os.path.join(tmp, "missing.json"), "--out-body", os.path.join(tmp, "b.md"),
                     "--out-headline", os.path.join(tmp, "h.json")])
    check("the CLI copes with a missing result file", (rc, json.load(open(os.path.join(tmp, "h.json")))["outcome"]), (0, "infra"))

print()
if failures:
    print(f"  *** {len(failures)} FAILURE(S) ***")
    for f in failures:
        print(f"      {f}")
    sys.exit(1)
print("  ALL CASES CORRECT")
