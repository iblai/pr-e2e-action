#!/usr/bin/env python3
"""Turn a PR e2e result into what the developer reads: the PR comment, and the one-line headline
shown on the run page and in the merge box.

    render.py --result pr-e2e-result.json --out-body body.md --out-headline headline.json

Reads the central run's pr-e2e-result.json (schema 2; older central runs wrote only the v1 fields,
and a run that never started wrote nothing) and what this caller knows itself, from the environment:
DISPATCH_CONCLUSION, JOB_STATUS, RUN_ID, HEAD_SHA, EVICTIONS, PREV_REPORT. No network: action.yml posts.
"""

import argparse
import json
import os
import re
import sys

README = "https://github.com/iblai/pr-e2e-action#reading-your-pr-e2e-result"
OPS_RUN = "https://github.com/iblai/iblai-deploy-ops/actions/runs/{}"
REPORT_LINK = "Full report, traces and screenshots"   # PREV_REPORT is found by this label: keep it
RETRY = "Nothing to change in your PR. Comment `/retest` to try again; if it fails the same way, tell the ops team."

# What this caller knows when the central run left no result to read.
CALLER_STATES = {
    "no_slot": ("infra", "Not your code: the shared test environment stayed busy for 2 hours, so this run never started."),
    "dispatch_failed": ("infra", "Not your code: the test pipeline didn't start this run."),
    "runner_unavailable": ("infra", "Not your code: the test runners are offline, so this run never started."),
    "timed_out": ("unclear", "The run took too long and was stopped before it finished."),
}
TITLES = {
    "passed": "✅ PR E2E passed",
    "failed": "❌ PR E2E failed",
    "failed_setup": "❌ PR E2E couldn't run your build",
    "infra": "⚠️ PR E2E: infrastructure problem, not your code",
    "cancelled": "⏹️ PR E2E stopped",
    "unknown": "❓ PR E2E failed",
}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def normalize(result, dispatch="", job_status="", evictions=0):
    """A schema-2 view of whatever there is: a v2 result, a v1 result, or nothing at all."""
    r = dict(result or {})
    if (_int(r.get("schema")) or 0) >= 2:
        return r
    view = {**r, "schema": 1, "outcome": "unknown", "owner": "unclear", "headline": "", "advice": RETRY,
            "reason": "", "details": [], "env_warning": "", "stage_label": "", "tests": {}}
    against = f" against prod {r['baseline']}" if r.get("baseline") else ""
    verdict = r.get("verdict") or ""
    if verdict == "success":
        view.update(outcome="passed", owner="none", advice="", headline=f"All tests passed{against}.")
    elif verdict == "failure" and (r.get("gate") == "FAIL" or r.get("report_url")):
        n = _int(r.get("new_failures")) or _int(r.get("failed")) or 0
        view.update(outcome="failed", owner="pr", pattern="tests_failed",
                    headline=(f"{plural(n, 'test')} failed{against}." if n else f"Tests failed{against}."),
                    advice="Open the report: every failure has a screenshot and a trace. "
                           "Comment `/retest <spec>` to re-run one file in minutes.")
    elif not r and dispatch in CALLER_STATES:
        owner, headline = CALLER_STATES[dispatch]
        view.update(outcome="infra" if owner == "infra" else "unknown", owner=owner, headline=headline)
    elif dispatch == "cancelled" or job_status == "cancelled":
        view.update(outcome="cancelled", owner="none", headline="This run was stopped before it finished.",
                    advice="Usually a newer commit or another `/retest` replaced it. "
                           "Comment `/retest` if you still need a result.")
    elif r:
        # An older central run that died before the suite: it never said why.
        view.update(headline="The run stopped before the tests ran.")
    else:
        view.update(headline="The run failed, and its result couldn't be read.")
    if evictions and view["outcome"] not in ("passed", "failed"):
        view["details"] = [f"This run was pushed out of the queue {plural(evictions, 'time')} by other PRs' runs before it started."]
    return view


def neutral(text):
    """Plain text from a pipeline must not mention people or link issues on a public PR."""
    return re.sub(r"#(?=\d)", "#\u200b", str(text).replace("@", "@\u200b"))


def code(text):
    text = " ".join(str(text).split())
    fence = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def block(lines):
    body = "\n".join(str(line) for line in lines)
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", body)), default=0) + 1)
    return [f"{fence}text", body, fence]


def title_key(v):
    if v["outcome"] == "failed" and v.get("pattern") not in (None, "", "tests_failed"):
        return "failed_setup"
    return v["outcome"] if v["outcome"] in TITLES else "unknown"


def marker_for(v):
    # A real result is a NEW comment, so the developer is notified and the history is kept. Only a
    # stopped run updates the single sticky notice: a label recycle fires two events, and cancelled
    # dispatches would otherwise spam the thread.
    return "<!-- pr-e2e-norun -->" if v["outcome"] == "cancelled" else "<!-- pr-e2e-report -->"


def tests_line(t):
    if not t:
        return ""
    parts = []
    if t.get("passed") is not None:
        parts.append(f"{t['passed']} passed")
    if t.get("failed") is not None:
        extra = ", ".join(x for x in (f"{t['new']} new" if t.get("new") is not None and t.get("known") else "",
                                      f"{t['known']} known" if t.get("known") else "") if x)
        parts.append(f"{t['failed']} failed" + (f" ({extra})" if extra else ""))
    for key in ("flaky", "skipped"):
        if t.get(key):
            parts.append(f"{t[key]} {key}")
    return " · ".join(parts)


def render(v, ctx):
    """The comment body (markdown) and the headline record."""
    report = v.get("report_url") or ""
    label = "Test log" if report.endswith(".log") else REPORT_LINK
    lines = [marker_for(v), f"### {TITLES[title_key(v)]}", "", f"**{neutral(v['headline'])}**"]
    if v.get("advice"):
        lines += ["", neutral(v["advice"])]
    if report:
        lines += ["", f"**[{label}]({report})**"]
    counts = tests_line(v.get("tests") or {})
    if counts:
        lines += ["", counts]
    if v.get("env_warning"):
        lines += ["", f"> ⚠️ {neutral(v['env_warning'])}"]
    if not report and ctx.get("prev_report") and v["outcome"] != "passed":
        lines += ["", f"Last completed run for this PR: **[report]({ctx['prev_report']})**, still valid unless this PR "
                      "has changed since."]
    details = []
    if v.get("stage_label"):
        details.append(f"- **Where it stopped:** {neutral(v['stage_label'])}")
    if v.get("reason"):
        details.append(f"- **Error:** {code(v['reason'])}")
    if v.get("details"):
        details += [""] + block(v["details"])
    if ctx.get("run_id"):
        details += ([""] if details else []) + [
            f"Pipeline run, for maintainers: [{ctx['run_id']}]({OPS_RUN.format(ctx['run_id'])})"]
    if details:
        lines += ["", "<details><summary>Details</summary>", ""] + details + ["", "</details>"]
    foot = []
    if ctx.get("head_sha"):
        foot.append(f"commit `{ctx['head_sha'][:8]}`")
    if v.get("platform"):
        foot.append(f"suite `{v['platform']}`" + (f" ({v['mode']})" if v.get("mode") else "")
                    + (f" on `{v['target_env']}`" if v.get("target_env") else ""))
    if v.get("baseline"):
        foot.append(f"tested against prod `{v['baseline']}`" + (" (env synced to it first)" if v.get("sync") == "success" else ""))
    foot.append(f"[How to read this]({README})")
    lines += ["", "<sub>" + " · ".join(foot) + "</sub>"]
    head = {"headline": neutral(v["headline"])[:140], "outcome": v["outcome"], "owner": v.get("owner", "unclear"),
            "report_url": report, "marker": marker_for(v)}
    return "\n".join(lines) + "\n", head


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--result", required=True, help="pr-e2e-result.json (may be missing)")
    ap.add_argument("--out-body", required=True)
    ap.add_argument("--out-headline", required=True)
    args = ap.parse_args(argv)
    try:
        with open(args.result, encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError):
        result = {}
    e = os.environ
    view = normalize(result, e.get("DISPATCH_CONCLUSION", ""), e.get("JOB_STATUS", ""), _int(e.get("EVICTIONS")) or 0)
    body, head = render(view, {"run_id": e.get("RUN_ID", ""), "head_sha": e.get("HEAD_SHA", ""),
                               "prev_report": e.get("PREV_REPORT", "")})
    with open(args.out_body, "w", encoding="utf-8") as f:
        f.write(body)
    with open(args.out_headline, "w", encoding="utf-8") as f:
        json.dump(head, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
