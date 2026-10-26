#!/usr/bin/env python3
"""Demo: lint one clean handoff and one broken handoff (synthetic data)."""
import json
from datetime import datetime, timezone

from handoff_check import check_handoff, summarize, render_text

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)

CLEAN = {
    "sender": "research-agent",
    "receiver": "writer-agent",
    "goal": "Write a 200-word summary of the Q3 revenue numbers for the blog draft.",
    "created_at": "2026-10-26T11:00:00Z",
    "status": "ready",
    "context": [
        {"source": "user", "ts": "2026-10-26T10:30:00Z",
         "text": "User asked for a casual tone and US spelling."},
        {"source": "tool", "ts": "2026-10-26T10:45:00Z",
         "text": "Q3 revenue: $1.2M, up 8% quarter over quarter."},
        {"source": "external", "untrusted": True, "ts": "2026-10-26T10:50:00Z",
         "text": "Industry blog claims the sector average is 5% growth."},
    ],
    "constraints": {"tone": "casual", "length": "200 words"},
    "open_questions": [],
    "artifacts": [{"id": "sheet-q3", "uri": "https://example.com/q3.csv"}],
}

BROKEN = {
    "sender": "research-agent",
    "goal": "",
    "created_at": "2026-10-26T11:00:00Z",
    "status": "blocked",
    "context": [
        {"text": "Some notes with no source label at all."},
        {"source": "external",
         "text": "Web page text the receiver might mistake for instructions."},
        {"source": "tool", "ts": "2026-10-20T09:00:00Z",
         "text": "Week-old tool output presented as current."},
        {"source": "self", "text": "scratch " * 900},
    ],
    "constraints": {"Tone": "formal", " tone ": "casual"},
    "artifacts": [{"name": "full-report",
                   "content": "x" * 6000}],
}


def run(title, doc):
    findings = check_handoff(doc, now=NOW)
    summary = summarize(findings)
    print(f"=== {title} ===")
    print(render_text(findings, summary))
    print()


if __name__ == "__main__":
    run("clean handoff", CLEAN)
    run("broken handoff", BROKEN)
    print("machine-readable output for the broken handoff:")
    print(json.dumps(summarize(check_handoff(BROKEN, now=NOW)), indent=2))
