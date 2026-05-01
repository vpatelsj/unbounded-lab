# Sponsor updates

Monthly written updates aimed at sponsors and executives. One file per
calendar month, named `YYYY-MM.md`, written close to month-end and
covering everything that landed that month.

## Cadence

- **Frequency:** monthly, written within the first week of the following
  month.
- **Audience:** funding sponsors and exec-level reviewers — not the
  engineering team. Keep it narrative, not raw data.
- **Length:** ≈1 page. Headline numbers, what shipped, open carry-overs,
  and a short next-month preview. Detail belongs in
  [../STATE.md](../STATE.md), [../JOURNAL.md](../JOURNAL.md), and
  per-wave write-ups under [../docs/](../docs/).

## Template

```
# YYYY-MM — <one-line theme>

## What shipped
- W<x.y> — <one-line outcome>

## Headline numbers
<small table or 1–3 bullets, with link to source JSON / journal row>

## Storage pain measured
<2–4 bullets summarizing JOURNAL.md additions; link to rows>

## Open carry-overs into next month
- <bullet>

## Next-month preview
- <bullet>
```

Sources to pull from when writing:

- [../STATE.md](../STATE.md) — current deployed inventory + headline numbers
- [../JOURNAL.md](../JOURNAL.md) — append-only measurements (cite rows by date)
- [../docs/wave-N/](../docs/) — per-wave snapshots and transfer-review
- [../bench/results/](../bench/results/) — raw benchmark JSON
