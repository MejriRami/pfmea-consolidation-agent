# PFMEA Consolidation Agent

Multi-agent system that consolidates Process FMEA (PFMEA) documents from several plants into one standard, and generates new PFMEAs from human-approved content, exposed to LLM assistants through an MCP server and a small web chat.

**▶ [Demo video, about 2 min](https://mejrirami.github.io/demos/#pfmea)**

> **Résumé (FR)** : système multi-agents qui consolide automatiquement les AMDEC Processus (PFMEA) de plusieurs sites en un référentiel standard, puis génère de nouvelles PFMEA à partir du seul contenu validé par un humain. Conçu et livré en entreprise ; ce dépôt est anonymisé et la démonstration utilise des données fictives.

![CI](https://github.com/MejriRami/pfmea-consolidation-agent/actions/workflows/tests.yml/badge.svg)

---

## The problem

A quality team maintained the same manufacturing process in **five PFMEA workbooks** from different plants and sources: different layouts, languages (French, English, Chinese in the same cell), rating methods (AP vs. legacy RPN) and wording. Building the standard PFMEA by hand meant reading **719 source rows** and deciding, row by row, which ones were duplicates and which ones added something new. The hand-made result, **192 validated rows**, became the answer key this system is measured against.

## What it does

| Capability | How |
|---|---|
| **Read any layout** | `MAPPER` agent maps each workbook's columns to the AIAG-VDA schema (header row, merged cells, URL-encoded sheet names, bilingual cells). |
| **Normalize** | `NORMALIZER` translates and cleans wording; `THEMER` assigns one theme from a frozen 42-theme vocabulary. |
| **Decide duplicates** | `ADJUDICATOR` compares every candidate row with the baseline rows of the same operation in one LLM call and returns *include / merge* with a justification and a confidence. **Below 0.85 → human review queue.** |
| **Rate conservatively** | Deterministic rules: worst-line-wins S/O/D, AP recomputed from the official AIAG-VDA lookup table (1000 entries). RPN-only rows are flagged `REQUIRES_MANUAL_RERATING`, never converted. |
| **Human approval** | Consolidated rows are written to `standard_line` as **draft**. Only `approve_standard_line(approved_by=…)`, which needs a named human reviewer, can set `approved`. No agent or MCP tool can. Every change is in `audit_log`. |
| **Generate** | `generate_pfmea` assembles a new PFMEA **only from approved lines** and writes an AIAG-VDA Excel file. Operations with no approved content get a flagged placeholder row; the agent never invents a line. |
| **Suggest** | `suggest_pfmea_additions` proposes extra failure modes for an operation, clearly labelled as AI suggestions that need validation. |

## Architecture

```
            Excel PFMEA files (5 layouts)
                       │
   Layer 1 — deterministic rules (no AI)
     operation resolution · dedup key · AP lookup · conservative rating
                       │
   Layer 2 — agents (OpenAI Agents SDK)
     MAPPER → NORMALIZER → THEMER → ADJUDICATOR ──(conf < 0.85)──► human review workbook
                       │
   PostgreSQL: standard_line (versioned, draft → approved by a human) + audit_log
                       │
   MCP server (12 typed tools, stdio or HTTP + bearer token)
        ├── terminal agent  (run_agent.py)
        ├── web chat        (webui/, streamed, with image upload)
        └── any MCP client  (Claude Desktop, etc.)
```

**Design choices**
- **Rules before LLMs.** Anything that has one right answer (operation code, dedup key, AP rating) is deterministic code with tests. LLMs are used only where judgment is needed (layout mapping, wording, duplicate decisions).
- **Measured, with go/no-go gates.** Each agent has a replay benchmark against the hand-made answer key (`pfmea_agents/eval_*.py`):
  - MAPPER: 100 % of mandatory fields mapped on the 5 known layouts;
  - THEMER: ≥ 90 % theme agreement, scored only over answer-key rows verified as internally consistent (building this eval uncovered 51 defective rows in the answer key itself, now documented in `ground_truth_alignment.py`);
  - ADJUDICATOR: ≥ 95 % recall on enrichment rows. If replay fails, the approach is wrong, and no UI work fixes it.
- **Never fabricate.** No RPN → AP conversion, no generated row without approved content, no silent guess on an ambiguous operation (it is routed to a human instead).

## Demo (fictional data)

The original pilot data belongs to a customer and is not published. `samples/seed_demo_db.py` creates a fictional coil / choke assembly standard (19 lines, 17 approved by a named reviewer, 2 left in draft) so the generation workflow can be run end to end.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # set DATABASE_URL (empty PostgreSQL DB) and OPENAI_API_KEY

python -m samples.seed_demo_db  # fictional standard lines + AP table
python -m webui.app             # chat UI on http://127.0.0.1:8001
```

Try in the chat:
1. *"I need a PFMEA for a new choke line: incoming material, tinning, glue dispensing and containment."*
2. The agent lists the operations that have approved content, proposes OP10 → OP50 → OP110 → OP200 and **waits for confirmation**.
3. After you confirm, it writes `exports/Generated_PFMEA_chokes.xlsx`: 9 validated lines, and OP200 flagged *"DRAFT - NO VALIDATED CONTENT"* instead of invented.

## Tests

```bash
pytest -q        # 188 passed, 89 skipped
```

Skipped tests need either a live database, an OpenAI key, or the confidential pilot answer key (`ground_truth/*.jsonl`, 719 → 192 rows). They run automatically when those are present; see `conftest.py`.

## Repository layout

| Path | Content |
|---|---|
| `rules/` | Deterministic layer: operations, dedup key, AP lookup, conservative rating, terminology |
| `pfmea_agents/` | Agents (mapper, normalizer, themer, adjudicator), pipeline, clustering, generation, replay evals |
| `db/` | SQLAlchemy models, versioned standard-line writer, human approval |
| `exports/` | AIAG-VDA Excel exports |
| `mcp_server/` | MCP server (FastMCP) exposing the tools; see its README |
| `webui/` | FastAPI streaming chat UI |
| `samples/` | Fictional demo data |
| `docs/DESIGN.md` | Design notes |

## Stack

Python · OpenAI Agents SDK · MCP (FastMCP) · PostgreSQL / SQLAlchemy · FastAPI · openpyxl · pytest · GitHub Actions

---

*Designed and built at my current employer; published with the code anonymized and with fictional demo data only.*
