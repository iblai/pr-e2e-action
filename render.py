#!/usr/bin/env python3
"""Turn a PR e2e result into what the developer reads: the PR comment, and the one-line headline
shown on the run page and in the merge box.

    render.py --result pr-e2e-result.json --out-body body.md --out-headline headline.json

Reads the central run's pr-e2e-result.json (schema 2; older central runs wrote only the v1 fields,
and a run that never started wrote nothing) and what this caller knows itself, from the environment: DISPATCH_CONCLUSION, JOB_STATUS, RUN_ID, HEAD_SHA,
EVICTIONS, PREV_REPORT, and --patch (the PR's package.json diff). No network: action.yml posts.
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
    # Failed tests (tests_failed, tests_failed_crash, …) vs. a build that never reached them.
    if v["outcome"] == "failed" and v.get("pattern") and not v["pattern"].startswith("tests_failed"):
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


KIND_LABEL = {"crash": "App crash", "js_error": "Browser error", "server_5xx": "Server error", "symptom": "Check failed"}
DEP = re.compile(r'^([-+])\s*"(@?[\w./-]+)"\s*:\s*"([^"]+)",?\s*$')


def deps_from_patch(patch):
    """Dependency versions a package.json diff changes: [(name, old, new)]."""
    old, new = {}, {}
    for line in (patch or "").splitlines():
        if line.startswith(("---", "+++")):
            continue
        m = DEP.match(line)
        if m:
            (old if m.group(1) == "-" else new)[m.group(2)] = m.group(3)
    return [(n, old[n], new[n]) for n in new if n in old and old[n] != new[n]]


def cell(text):
    """Table-safe: a pipe would end the cell, a newline the row."""
    return neutral(" ".join(str(text).split()).replace("|", "\\|"))


def causes_block(v, deps):
    """What really broke, grouped (central failure-causes.py): new causes as a table, known ones apart."""
    causes = v.get("causes") or []
    new = [c for c in causes if not c.get("known")]
    known = [c for c in causes if c.get("known")]
    own = [c for c in new if not c.get("seen_elsewhere")]
    lines = []
    if new:
        lines += ["", "| Tests | What went wrong | Example |", "|---:|---|---|"]
        for c in new:
            ex = c.get("example") or {}
            test = cell((ex.get("title") or "").split(" › ")[-1])
            links = " · ".join(f"[{label}]({ex[k]})" for k, label in (("screenshot_url", "screenshot"), ("trace_url", "trace"))
                               if ex.get(k))
            where = f" at {code(c['where'])}" if c.get("where") else ""
            if c.get("seen_elsewhere"):
                where += f" — also failing on {cell(', '.join(c['seen_elsewhere']))}, probably not yours"
            lines.append(f"| {c.get('count', 0)} | {KIND_LABEL.get(c.get('kind'), 'Failed')}: {code(c.get('text', '')).replace('|', chr(92) + '|')}"
                         f"{where} | {test}{' · ' + links if links else ''} |")
        top = own[0] if own else new[0]
        if top.get("snippet"):
            lines += ["", "Code at that spot:"] + block([top["snippet"]])
        if own and top.get("kind") in ("crash", "js_error") and deps:
            change = ", ".join(f"{code(n)} {code(a)} → {code(b)}" for n, a, b in deps[:3])
            lines += ["", f"**Hint:** this PR changes {change}. A crash right after a dependency bump usually comes "
                          "from the new version."]
    if known:
        lines += ["", "Probably not from your PR (known issues): "
                  + "; ".join(f"{c.get('count', 0)} × {neutral(c.get('plain', ''))}" for c in known)]
    return lines


EARLY = 10  # tests sharing one crash or browser error before the PR is warned


def progress_line(live):
    """One line for the caller's log while the suite runs."""
    line = f"e2e progress: {live.get('passed', 0)} passed, {live.get('failed', 0)} failed so far"
    top = (live.get("causes") or [None])[0]
    if top:
        line += f"; most common: {KIND_LABEL.get(top.get('kind'), top.get('kind'))} x{top.get('count')} {code(top.get('text', '')[:80])}"
    return line


def early_warning(live, threshold=EARLY):
    """The one comment posted mid-run when a crash or browser error has already hit `threshold` tests."""
    top = (live.get("causes") or [None])[0]
    if not top or top.get("kind") not in ("crash", "js_error") or (top.get("count") or 0) < threshold:
        return None
    what = "crashes" if top["kind"] == "crash" else "throws a browser error"
    return "\n".join([
        "<!-- pr-e2e-early -->",
        "### ⚠️ PR E2E early warning: your build looks broken",
        "",
        f"**{top['count']} tests so far: your build {what}: {code(top.get('text', ''))}**",
        "",
        f"The run keeps going ({live.get('passed', 0)} passed, {live.get('failed', 0)} failed so far). The full result, "
        "with every cause, a screenshot and a trace, is posted when it finishes. You can fix and push now; the push "
        "starts a new run.",
    ]) + "\n"


def render(v, ctx):
    """The comment body (markdown) and the headline record."""
    report = v.get("report_url") or ""
    label = "Test log" if report.endswith(".log") else REPORT_LINK
    lines = [marker_for(v), f"### {TITLES[title_key(v)]}", "", f"**{neutral(v['headline'])}**"]
    if v.get("advice"):
        lines += ["", neutral(v["advice"])]
    lines += causes_block(v, ctx.get("deps") or [])
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
    ap.add_argument("--result", help="pr-e2e-result.json (may be missing)")
    ap.add_argument("--out-body")
    ap.add_argument("--out-headline")
    ap.add_argument("--live", help="progress mode: the run's live.json; prints a progress line")
    ap.add_argument("--out-warning", help="progress mode: write the early-warning comment here when it is due")
    ap.add_argument("--patch", default="", help="the PR's package.json diff, for the dependency-bump hint")
    args = ap.parse_args(argv)
    if args.live:
        try:
            with open(args.live, encoding="utf-8") as f:
                live = json.load(f)
        except (OSError, ValueError):
            return 0
        print(progress_line(live))
        warning = early_warning(live)
        if warning and args.out_warning:
            with open(args.out_warning, "w", encoding="utf-8") as f:
                f.write(warning)
        return 0
    if not (args.result and args.out_body and args.out_headline):
        ap.error("--result, --out-body and --out-headline are required (or --live)")
    try:
        with open(args.result, encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError):
        result = {}
    e = os.environ
    view = normalize(result, e.get("DISPATCH_CONCLUSION", ""), e.get("JOB_STATUS", ""), _int(e.get("EVICTIONS")) or 0)
    patch = ""
    if args.patch:
        try:
            with open(args.patch, encoding="utf-8") as f:
                patch = f.read()
        except OSError:
            pass
    body, head = render(view, {"run_id": e.get("RUN_ID", ""), "head_sha": e.get("HEAD_SHA", ""),
                               "prev_report": e.get("PREV_REPORT", ""), "deps": deps_from_patch(patch)})
    with open(args.out_body, "w", encoding="utf-8") as f:
        f.write(body)
    with open(args.out_headline, "w", encoding="utf-8") as f:
        json.dump(head, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
