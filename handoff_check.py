#!/usr/bin/env python3
"""agent-handoff-check: lint agent-to-agent handoff documents.

A handoff is a JSON document one agent passes to another when delegating
work. This tool checks three properties of the document:

  completeness  - does the receiving agent get what it needs to start?
  provenance    - is every context entry labeled with where it came from,
                  and is external content explicitly marked untrusted?
  minimality    - does the handoff carry opaque blobs and inline payloads
                  that should have been references?

All checks are heuristics over a documented JSON convention. They catch
common handoff defects; they do not prove a handoff is safe or correct.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone

VALID_SOURCES = {"user", "tool", "external", "self"}
DEFAULT_MAX_INLINE = 4000
DEFAULT_STALE_HOURS = 24.0
ENTROPY_MIN_LEN = 100
ENTROPY_WARN_BITS = 4.5

ERROR = "error"
WARN = "warning"
INFO = "info"


def shannon_entropy(text: str) -> float:
    """Bits per character. English prose is ~4.0-4.3, base64/hex ~5-6."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are treated as UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _finding(rule: str, severity: str, path: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "path": path, "message": message}


def _scan_text(rule_path: str, text: str, max_inline: int, findings: list) -> None:
    if len(text) > max_inline:
        findings.append(_finding(
            "inline-payload", WARN, rule_path,
            f"{len(text)} chars of inline content (limit {max_inline}); "
            "large payloads should travel as references (uri/path/id), not copies"))
    if len(text) >= ENTROPY_MIN_LEN and shannon_entropy(text) > ENTROPY_WARN_BITS:
        findings.append(_finding(
            "high-entropy-blob", WARN, rule_path,
            f"opaque high-entropy string ({shannon_entropy(text):.2f} bits/char); "
            "handoffs should carry references, not blobs"))


def check_handoff(doc, max_inline: int = DEFAULT_MAX_INLINE,
                  stale_hours: float = DEFAULT_STALE_HOURS,
                  now: datetime | None = None) -> list:
    """Return findings for one handoff document."""
    findings: list[dict] = []
    if not isinstance(doc, dict):
        return [_finding("invalid-document", ERROR, "$", "handoff must be a JSON object")]

    created = parse_ts(doc.get("created_at"))
    clock = now or created or datetime.now(timezone.utc)
    stale_before = clock - timedelta(hours=stale_hours)

    # --- participants -------------------------------------------------
    for field in ("sender", "receiver"):
        value = doc.get(field)
        if not (isinstance(value, str) and value.strip()):
            findings.append(_finding(
                f"missing-{field}", ERROR, f"$.{field}",
                f"handoff must name its {field} (role or id)"))

    # --- goal ---------------------------------------------------------
    goal = doc.get("goal")
    if not (isinstance(goal, str) and goal.strip()):
        findings.append(_finding(
            "missing-goal", ERROR, "$.goal",
            "handoff needs a non-empty goal so the receiver knows what done means"))

    # --- context entries ----------------------------------------------
    context = doc.get("context")
    if context is None:
        context = []
    if not isinstance(context, list):
        findings.append(_finding(
            "invalid-context", ERROR, "$.context", "context must be a list of entries"))
        context = []
    if not context:
        findings.append(_finding(
            "missing-context", WARN, "$.context",
            "no context entries; the receiver starts cold"))

    for i, entry in enumerate(context):
        path = f"$.context[{i}]"
        if not isinstance(entry, dict):
            findings.append(_finding(
                "invalid-context-entry", ERROR, path, "context entry must be an object"))
            continue
        source = entry.get("source")
        if source not in VALID_SOURCES:
            findings.append(_finding(
                "unlabeled-source", ERROR, f"{path}.source",
                "context entry needs a source label: user, tool, external, or self"))
        elif source == "external" and entry.get("untrusted") is not True:
            findings.append(_finding(
                "unmarked-external", ERROR, path,
                'external content must carry "untrusted": true so the receiver '
                "treats it as data, not instructions"))
        text = entry.get("text")
        if isinstance(text, str):
            _scan_text(f"{path}.text", text, max_inline, findings)
        entry_ts = parse_ts(entry.get("ts"))
        if entry_ts is not None and entry_ts < stale_before:
            findings.append(_finding(
                "stale-context", WARN, f"{path}.ts",
                f"context is older than {stale_hours:g}h; confirm it is still true"))

    # --- constraints ----------------------------------------------------
    constraints = doc.get("constraints")
    if isinstance(constraints, dict):
        seen: dict[str, object] = {}
        for key, value in constraints.items():
            norm = " ".join(str(key).lower().split())
            if norm in seen and seen[norm] != value:
                findings.append(_finding(
                    "constraint-conflict", WARN, "$.constraints",
                    f'constraint "{key}" conflicts with an earlier spelling of the '
                    "same constraint"))
            seen[norm] = value
    elif isinstance(constraints, list):
        lowered = [str(c).strip().lower() for c in constraints]
        for c in set(lowered):
            if lowered.count(c) > 1:
                findings.append(_finding(
                    "constraint-duplicate", INFO, "$.constraints",
                    f'constraint repeated: "{c}"'))

    # --- open questions ---------------------------------------------------
    status = str(doc.get("status", "")).lower()
    open_qs = doc.get("open_questions")
    if status in {"blocked", "waiting"} and not open_qs:
        findings.append(_finding(
            "missing-open-questions", INFO, "$.open_questions",
            f'status is "{status}" but no open_questions list what is unresolved'))

    # --- artifacts ----------------------------------------------------------
    artifacts = doc.get("artifacts")
    if isinstance(artifacts, list):
        for i, art in enumerate(artifacts):
            path = f"$.artifacts[{i}]"
            if not isinstance(art, dict):
                findings.append(_finding(
                    "invalid-artifact", ERROR, path, "artifact must be an object"))
                continue
            if not any(art.get(k) for k in ("id", "uri", "path")):
                findings.append(_finding(
                    "artifact-without-reference", WARN, path,
                    "artifact has no id/uri/path reference"))
            content = art.get("content")
            if isinstance(content, str):
                _scan_text(f"{path}.content", content, max_inline, findings)

    order = {ERROR: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["rule"], f["path"]))
    return findings


def summarize(findings: list) -> dict:
    out = {"errors": 0, "warnings": 0, "infos": 0}
    for f in findings:
        out[{ERROR: "errors", WARN: "warnings", INFO: "infos"}[f["severity"]]] += 1
    out["ok"] = out["errors"] == 0
    return out


def render_text(findings: list, summary: dict) -> str:
    lines = [f"agent-handoff-check: {summary['errors']} error(s), "
             f"{summary['warnings']} warning(s), {summary['infos']} info"]
    for f in findings:
        lines.append(f"[{f['severity']}] {f['rule']} at {f['path']}: {f['message']}")
    if not findings:
        lines.append("no findings")
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint an agent-to-agent handoff JSON document.")
    parser.add_argument("handoff", help="path to the handoff JSON file, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--max-inline", type=int, default=DEFAULT_MAX_INLINE,
                        help=f"inline content length limit (default {DEFAULT_MAX_INLINE})")
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS,
                        help=f"context staleness threshold in hours (default {DEFAULT_STALE_HOURS:g})")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if args.handoff == "-" else open(args.handoff, encoding="utf-8").read()
    except OSError as exc:
        print(f"error: cannot read {args.handoff}: {exc}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2

    findings = check_handoff(doc, max_inline=args.max_inline, stale_hours=args.stale_hours)
    summary = summarize(findings)
    if args.json:
        print(json.dumps({"ok": summary["ok"], "summary": summary, "findings": findings}, indent=2))
    else:
        print(render_text(findings, summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
