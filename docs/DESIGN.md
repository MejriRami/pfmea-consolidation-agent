# Design notes

These notes summarise the specification the system was built against. Section numbers
quoted in code comments (`spec §…`) refer to the original internal specification; the
decisions that matter are reproduced here.

## Goal

Reproduce automatically the consolidation a quality team had done by hand: many PFMEA
source rows from several plants → one standard PFMEA, with traceability and a
justification for every inclusion or exclusion. The hand-made master is the answer key.
**If the agent cannot replay it, it ships nothing.**

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Conservative rating | Worst line wins: take S/O/D from the highest-severity source row (tiebreak: highest AP), then recompute AP with the official lookup table. |
| 2 | Theme vocabulary | Frozen closed set of 42 themes. Anything else → `UNCLASSIFIED`. |
| 3 | Adjudicator threshold | 0.85. Below it, the decision goes to a human review workbook. |
| 4 | RPN-only rows | Flagged `REQUIRES_MANUAL_RERATING`. RPN → AP is not invertible; never fabricate. |
| 5 | Approval | Human only. No MCP tool may write `status='approved'` (§7). |
| 6 | Generation | Only approved lines; missing operations are flagged, never filled in by the model. |

## Two layers

**Layer 1: deterministic rules (`rules/`).** Operation resolution by weighted keyword
matching against the canonical process flow, dedup key construction, AP lookup,
conservative rating, terminology. Returns *None* on ambiguity instead of guessing; an
ambiguous row is routed to a human or to the AI disambiguation step.

**Layer 2: agents (`pfmea_agents/`).** MAPPER (column mapping per layout), NORMALIZER
(translation/cleanup), THEMER (theme from the frozen vocabulary), ADJUDICATOR (include vs.
merge against same-operation baseline rows, with justification and confidence).

## Evaluation gates (§8)

| Phase | Agent | Done when |
|---|---|---|
| 2 | MAPPER | 100 % mandatory-field mapping on the 5 known layouts |
| 3 | THEMER | ≥ 0.90 theme agreement vs. clean ground truth (alignment-verified rows only) |
| 4 | ADJUDICATOR | replay reproduces the master's decisions, enrichment recall ≥ 0.95 |

A lesson from the replay: mechanical pre-filters (exact dedup keys, embedding similarity,
operation + theme buckets) each failed for structural reasons, so every candidate is now
compared with every same-operation baseline row in one adjudicator call. See the module
docstring of `pfmea_agents/pipeline.py`.

## Answer-key hygiene

Building the evals exposed defects in the hand-made answer key (re-themed rows, an ID
column that drifted out of sync with the data after a sort, rows whose theme belonged to
another row). These are documented and handled explicitly (`retheme_decisions.py`,
`pfmea_agents/ground_truth_alignment.py`) rather than silently "fixed" in the data.
