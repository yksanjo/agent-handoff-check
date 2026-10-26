# agent-handoff-check

Lint agent-to-agent handoff documents before the next agent picks them up.

When one AI agent delegates work to another, it passes a handoff: a goal,
some context, constraints, open questions, references to artifacts. Handoffs
fail in boring ways - the goal is missing, context arrives with no label
saying where it came from, a web page's text sits next to the user's
instructions with nothing marking it as external, or a 50 KB blob gets
pasted inline where a reference would do. The receiving agent then either
starts cold or, worse, treats untrusted text as instructions.

`agent-handoff-check` checks a handoff JSON document for three properties:

- **completeness** - goal, sender, receiver, and context are present
- **provenance** - every context entry is labeled `user`, `tool`,
  `external`, or `self`, and external content carries `"untrusted": true`
- **minimality** - large payloads and opaque high-entropy blobs travel as
  references (`id` / `uri` / `path`), not inline copies

Exit code is CI-friendly: `0` when no errors, `1` on any error, `2` for
usage or parse problems.

## Requirements

Python 3.10+. Standard library only - no dependencies to install.

## Usage

```
python3 handoff_check.py handoff.json
python3 handoff_check.py handoff.json --json
python3 handoff_check.py handoff.json --max-inline 8000 --stale-hours 48
cat handoff.json | python3 handoff_check.py -
```

Run the demo (synthetic data, no network):

```
python3 demo.py
```

Run the tests:

```
python3 -m unittest test_handoff_check
```

## Handoff convention

The tool expects this shape (all fields optional except the ones the rules
require):

```json
{
  "sender": "research-agent",
  "receiver": "writer-agent",
  "goal": "Write a 200-word summary of the Q3 numbers.",
  "created_at": "2026-10-26T11:00:00Z",
  "status": "ready",
  "context": [
    {"source": "user",  "ts": "2026-10-26T10:30:00Z", "text": "casual tone"},
    {"source": "external", "untrusted": true, "text": "blog claims 5% growth"}
  ],
  "constraints": {"tone": "casual"},
  "open_questions": [],
  "artifacts": [{"id": "sheet-q3", "uri": "https://example.com/q3.csv"}]
}
```

This is a documented convention for this linter, not an industry standard.
Adapt the field names to your system or treat the rules as a checklist.

## Rules

| rule | severity | what it catches |
|------|----------|-----------------|
| `invalid-document` / `invalid-context` / `invalid-artifact` | error | wrong JSON shape |
| `missing-sender` / `missing-receiver` | error | unnamed handoff participants |
| `missing-goal` | error | receiver cannot tell what "done" means |
| `unlabeled-source` | error | context entry with no/unknown source label |
| `unmarked-external` | error | external content not marked `"untrusted": true` |
| `missing-context` | warning | receiver starts cold |
| `inline-payload` | warning | inline content over `--max-inline` chars (default 4000) |
| `high-entropy-blob` | warning | opaque high-entropy string (possible blob; heuristic) |
| `stale-context` | warning | context older than `--stale-hours` (default 24) |
| `constraint-conflict` | warning | same constraint restated with a different value |
| `artifact-without-reference` | warning | artifact with no `id`/`uri`/`path` |
| `constraint-duplicate`, `missing-open-questions` | info | hygiene |

## Limits

- These are heuristics, not a proof that a handoff is safe or correct. A
  handoff with zero findings can still contain bad instructions; findings
  can also be false positives (the entropy check fires on dense data, not
  just secrets).
- The `high-entropy-blob` check is a minimality signal ("carry a reference,
  not a blob"), not a secret detector. It does not establish that a handoff
  contains no credentials or private data.
- The tool reads one document; it does not verify that referenced artifacts
  exist, that quoted facts are true, or that the sender was authorized to
  delegate the goal.
- All demo and test data is synthetic and fictional.

## License

MIT - see [LICENSE](LICENSE).
