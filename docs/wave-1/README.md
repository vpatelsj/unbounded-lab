# Wave 1 — frozen snapshot

Wave 1 closed on 2026-04-30. The files in this folder are a **historical
snapshot**: the architecture, state, demo script, transfer-review, and
W1.2 sanity write-up as they stood at wave close. They are not updated
in place anymore.

For wave-agnostic, currently-true documentation see the root-level
canonical set:

- [ARCHITECTURE.md](../../ARCHITECTURE.md) — wave-agnostic topology, conventions, hard rules
- [STATE.md](../../STATE.md) — what's deployed right now
- [ROADMAP.md](../../ROADMAP.md) — strategy, future waves, transfer plan
- [GLOSSARY.md](../../GLOSSARY.md) — canonical model / namespace / label names
- [JOURNAL.md](../../JOURNAL.md) — append-only storage-pain measurements

## Files in this folder

| File                                            | What it captured at Wave 1 close                                            |
|-------------------------------------------------|-----------------------------------------------------------------------------|
| [architecture.md](architecture.md)              | Mermaid topology + first-party Azure surfaces table at wave close           |
| [state.md](state.md)                            | Endpoints, PVCs, headline numbers — superseded by [STATE.md](../../STATE.md) |
| [demo.md](demo.md)                              | 10-minute live walkthrough hitting every W1.x item                          |
| [transfer-review.md](transfer-review.md)        | Per-W1-item GB200/GB300 transplant cost                                     |
| [w1.2-vllm-sanity.md](w1.2-vllm-sanity.md)      | vLLM throughput / context / batch sweep against the deployed config        |

The Wave 1 reboot runbook moved out of this folder because it remains an
operational playbook, not a historical artifact:
[docs/runbooks/spark-reboot.md](../runbooks/spark-reboot.md).
