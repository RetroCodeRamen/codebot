# Pipeline templates (`codebot.pipeline/v1`)

Shipped example agents, teams, workflows, and projects for modular orchestration.
JSON is the canonical form; the visual editor (later) will edit the same files.

Validate:

```
PYTHONPATH=lib python3 -m codebot.pipeline validate pipelines/templates/projects/*.json
```

| Project | What it shows |
|---|---|
| `projects/basic-calculator.json` | One developer → drafts-style build (scenario A) |
| `projects/spec-with-reviewers.json` | Architect + three goldfish reviewers, bounded review loop |
| `projects/spec-debate.json` | Same, but goldfish may QUESTION each other |
| `projects/small-pods.json` | Coordinator + math/UI pods (scenario B shape) |
| `projects/nested-pods.json` | Coordinator with a nested team expanded to `math-impl` / `math-check` |

Install onto a project: `codebot pipeline init <name> --template solo|architect-reviewers|debate|small-team`. GUI: `#/pipeline/<name>`. See `docs/architecture/modular-agent-orchestration.md`.
