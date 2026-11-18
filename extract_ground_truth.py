"""
PHASE 0 — Ground truth extraction.

Reads the hand-consolidated master workbook and emits the answer key that
every later phase is measured against.

Outputs (to ./out/):
  raw_lines.jsonl        729 source rows from 01_ALL_SOURCES_GROUPED
  final_lines.jsonl      202 consolidated rows from 02_FINAL_PFMEA
  decisions.jsonl        one decision per raw row: INCLUDE / EXCLUDE + why
  ap_lookup.json         1000-row deterministic S/O/D -> AP table
  vocabulary.json        frozen theme vocabulary (42 themes)
  stats.json             counts for sanity checking

Run:  python extract_ground_truth.py --master <path.xlsx>
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import openpyxl

from retheme_decisions import RETHEME, THEME_VOCABULARY, UNCLASSIFIED

# ── Column map for 02_FINAL_PFMEA (header row 9, data from row 10) ─────────
FINAL_COLS = {
    "row_id": 0,            # A
    "history": 1,           # B
    "process_item": 2,      # C
    "process_step": 3,      # D
    "work_element": 4,      # E
    "function_item": 5,     # F
    "function_step": 6,     # G
    "function_element": 7,  # H
    "failure_mode": 8,      # I
    "failure_effect": 9,    # J
    "severity": 10,         # K
    "failure_cause": 11,    # L
    "prevention_control": 12,  # M
    "occurrence": 13,       # N
    "detection_control": 14,   # O
    "detection": 15,        # P
    "ap": 16,               # Q
    "special_char": 17,     # R
    "filter_code": 18,      # S
    "source_basis": 32,     # AG
    "pfc_link": 33,         # AH
    "qg_link": 34,          # AI
    "expert_value_add": 35, # AJ
    "validation_status": 36,# AK
    "source_row_refs": 37,  # AL
    "op_sequence_index": 41,# AP
    "primary_source": 42,   # AQ
    "source_color": 43,     # AR
    "consolidation_tag": 44,# AS
    "master_comparator": 45,# AT
    "selected_source_rows": 46,  # AU
    "justification": 47,    # AV
    "dedup_key": 48,        # AW
}

FINAL_HEADER_ROW = 9
GROUPED_HEADER_ROW = 9

# Sources that never become PFMEA rows
PFC_ONLY_SOURCES = {"SITE_D_PFC"}
BASELINE_SOURCE = "MASTER_REF"

CJK = re.compile(r"[\u4e00-\u9fff]")


def cell(row, key, colmap=FINAL_COLS):
    idx = colmap[key]
    if len(row) <= idx:
        return None
    v = row[idx]
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def norm_text(s: str | None) -> str | None:
    """Collapse whitespace/newlines. Original wording preserved separately."""
    if s is None:
        return None
    return re.sub(r"\s+", " ", str(s)).strip() or None


def parse_dedup_key(raw: str | None):
    """AW format: OP10|theme|failure mode text"""
    if not raw or "|" not in str(raw):
        return None, None, None
    parts = str(raw).split("|")
    if len(parts) < 3:
        return None, None, None
    op = parts[0].strip()
    theme = parts[1].strip()
    fm = "|".join(parts[2:]).strip().lower()
    return op, theme, fm


def build_dedup_key(op: str, theme: str, failure_mode: str) -> str:
    """Canonical key construction. MUST match the rules engine exactly."""
    fm = re.sub(r"\s+", " ", (failure_mode or "")).strip().lower()
    return f"{op}|{theme}|{fm}"


SRC_REF = re.compile(r"^\s*([^:]+):([^@]+)@")


def parse_source_row_refs(raw: str | None):
    """
    Column AL holds the authoritative provenance:
        'MASTER_REF:00.0.31@01_ALL_SOURCES_GROUPED!R40; SUPPORTED_BY:...'
    Returns (source, row_id) from the FIRST (primary) reference.

    DEFECT IN MASTER (documented): column A (row_id) is offset from
    AG/AQ/AL for ~31 rows — the ID column drifted out of sync with the
    data during a sort/paste. AG, AQ and AL agree with each other and are
    internally consistent, so AL is authoritative. Column A is discarded
    for identity and kept only as `row_id_column_a_untrusted`.
    """
    if not raw:
        return None, None
    first = str(raw).split(";")[0]
    m = SRC_REF.match(first)
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def classify_decision(source: str, tag: str | None, comparator: str | None) -> str:
    if source == BASELINE_SOURCE:
        return "INCLUDE_BASELINE"
    if source in PFC_ONLY_SOURCES:
        return "EXCLUDE_PFC"
    return "INCLUDE_ENRICHMENT"


def extract_final(ws) -> list[dict]:
    out = []
    for i, row in enumerate(ws.iter_rows(min_row=FINAL_HEADER_ROW + 1,
                                         values_only=True), FINAL_HEADER_ROW + 1):
        rid = cell(row, "row_id")
        fm = cell(row, "failure_mode")
        if not rid or not fm:
            continue

        raw_key = cell(row, "dedup_key")
        op, theme, key_fm = parse_dedup_key(raw_key)

        # ---- apply Step 2 re-theming -----------------------------------
        retheme_applied = False
        if i in RETHEME:
            old, new, why = RETHEME[i]
            if theme != old:
                # master changed since the decisions were made — fail loudly
                raise ValueError(
                    f"row {i}: expected theme {old!r} for retheme, found {theme!r}. "
                    "retheme_decisions.py is stale.")
            theme = new
            retheme_applied = True

        if theme and theme not in THEME_VOCABULARY:
            theme = UNCLASSIFIED

        refs = norm_text(cell(row, "source_row_refs"))
        ref_source, ref_row_id = parse_source_row_refs(refs)

        # AL is authoritative; AG is a cross-check; A is untrusted (see docstring)
        source = ref_source or norm_text(cell(row, "source_basis")) or "UNKNOWN"
        row_id = ref_row_id or str(rid)

        ag_source = norm_text(cell(row, "source_basis"))
        source_disagreement = bool(ag_source and ref_source and ag_source != ref_source)
        id_drift = bool(ref_row_id and str(rid) != ref_row_id)

        rec = {
            "excel_row": i,
            "row_id": row_id,
            "row_id_column_a_untrusted": str(rid),
            "id_drift_detected": id_drift,
            "source_disagreement": source_disagreement,
            "source": source,
            "operation": op,
            "op_sequence_index": cell(row, "op_sequence_index"),
            "process_item": norm_text(cell(row, "process_item")),
            "process_step": norm_text(cell(row, "process_step")),
            "work_element": norm_text(cell(row, "work_element")),
            "function_item": norm_text(cell(row, "function_item")),
            "function_step": norm_text(cell(row, "function_step")),
            "function_element": norm_text(cell(row, "function_element")),
            "failure_mode": norm_text(fm),
            "failure_effect": norm_text(cell(row, "failure_effect")),
            "failure_cause": norm_text(cell(row, "failure_cause")),
            "prevention_control": norm_text(cell(row, "prevention_control")),
            "detection_control": norm_text(cell(row, "detection_control")),
            "severity": cell(row, "severity"),
            "occurrence": cell(row, "occurrence"),
            "detection": cell(row, "detection"),
            "ap": cell(row, "ap"),
            "special_char": cell(row, "special_char"),
            "theme": theme,
            "theme_retheme_applied": retheme_applied,
            "dedup_key_original": raw_key,
            "dedup_key": build_dedup_key(op, theme, key_fm) if op and theme else None,
            "consolidation_tag": norm_text(cell(row, "consolidation_tag")),
            "master_comparator": norm_text(cell(row, "master_comparator")),
            "justification": norm_text(cell(row, "justification")),
            "source_row_refs": norm_text(cell(row, "source_row_refs")),
            "selected_source_rows": norm_text(cell(row, "selected_source_rows")),
            "has_cjk": bool(CJK.search(str(fm))),
        }
        out.append(rec)
    return out


def extract_grouped(ws) -> list[dict]:
    out = []
    for i, row in enumerate(ws.iter_rows(min_row=GROUPED_HEADER_ROW + 1,
                                         values_only=True), GROUPED_HEADER_ROW + 1):
        rid = cell(row, "row_id")
        fm = cell(row, "failure_mode")
        if not rid or not fm:
            continue
        source = norm_text(cell(row, "source_basis")) or "UNKNOWN"
        out.append({
            "excel_row": i,
            "row_id": str(rid),
            "source": source,
            "process_step": norm_text(cell(row, "process_step")),
            "failure_mode": norm_text(fm),
            "failure_effect": norm_text(cell(row, "failure_effect")),
            "failure_cause": norm_text(cell(row, "failure_cause")),
            "severity": cell(row, "severity"),
            "occurrence": cell(row, "occurrence"),
            "detection": cell(row, "detection"),
            "ap": cell(row, "ap"),
            "has_cjk": bool(CJK.search(str(fm))),
        })
    return out


def extract_ap_lookup(ws) -> dict:
    table = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        s, o, d, ap = row[0], row[1], row[2], row[3]
        if None in (s, o, d, ap):
            continue
        table[f"{int(s)}-{int(o)}-{int(d)}"] = str(ap).strip()
    return table


def build_decisions(final: list[dict], grouped: list[dict]) -> list[dict]:
    """One decision per grouped row: did it survive into the final PFMEA?"""
    survivors = {(r["source"], r["row_id"]) for r in final}
    final_by_id = {(r["source"], r["row_id"]): r for r in final}

    decisions = []
    for g in grouped:
        k = (g["source"], g["row_id"])
        if k in survivors:
            f = final_by_id[k]
            decisions.append({
                "source": g["source"],
                "row_id": g["row_id"],
                "grouped_excel_row": g["excel_row"],
                "decision": classify_decision(
                    g["source"], f.get("consolidation_tag"),
                    f.get("master_comparator")),
                "final_excel_row": f["excel_row"],
                "dedup_key": f["dedup_key"],
                "theme": f["theme"],
                "justification": f["justification"],
            })
        else:
            decisions.append({
                "source": g["source"],
                "row_id": g["row_id"],
                "grouped_excel_row": g["excel_row"],
                "decision": ("EXCLUDE_PFC" if g["source"] in PFC_ONLY_SOURCES
                             else "EXCLUDE_DUPLICATE"),
                "final_excel_row": None,
                "dedup_key": None,
                "theme": None,
                "justification": None,
            })
    return decisions


def write_jsonl(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def main():
    ap_arg = argparse.ArgumentParser()
    ap_arg.add_argument("--master", required=True)
    ap_arg.add_argument("--out", default="out")
    args = ap_arg.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(args.master, data_only=True, read_only=True)

    final = extract_final(wb["02_FINAL_PFMEA"])
    grouped = extract_grouped(wb["01_ALL_SOURCES_GROUPED"])
    ap_table = extract_ap_lookup(wb["21_AP_LOOKUP"])
    decisions = build_decisions(final, grouped)

    write_jsonl(outdir / "final_lines.jsonl", final)
    write_jsonl(outdir / "raw_lines.jsonl", grouped)
    write_jsonl(outdir / "decisions.jsonl", decisions)
    (outdir / "ap_lookup.json").write_text(json.dumps(ap_table, indent=2))
    (outdir / "vocabulary.json").write_text(
        json.dumps(sorted(THEME_VOCABULARY), indent=2))

    # ---- stats -------------------------------------------------------
    dec_counts = Counter(d["decision"] for d in decisions)
    src_final = Counter(r["source"] for r in final)
    src_raw = Counter(r["source"] for r in grouped)
    retheme_n = sum(1 for r in final if r["theme_retheme_applied"])
    unclassified = sum(1 for r in final if r["theme"] == UNCLASSIFIED)
    cjk_final = sum(1 for r in final if r["has_cjk"])

    grouped_keys = {(r["source"], r["row_id"]) for r in grouped}
    orphans = [r for r in final if (r["source"], r["row_id"]) not in grouped_keys]

    stats = {
        "raw_rows": len(grouped),
        "final_rows": len(final),
        "decisions": dict(dec_counts),
        "master_defects": {
            "id_drift_rows": sum(1 for r in final if r["id_drift_detected"]),
            "source_disagreement_rows": sum(1 for r in final if r["source_disagreement"]),
            "orphan_final_rows": len(orphans),
            "orphan_examples": [
                {"excel_row": r["excel_row"], "source": r["source"],
                 "row_id": r["row_id"]} for r in orphans[:10]],
        },
        "raw_by_source": dict(src_raw),
        "final_by_source": dict(src_final),
        "retention_by_source": {
            s: round(src_final.get(s, 0) / n, 3) for s, n in src_raw.items()},
        "rethemed_rows": retheme_n,
        "unclassified_themes": unclassified,
        "final_rows_with_cjk_failure_mode": cjk_final,
        "ap_lookup_entries": len(ap_table),
        "vocabulary_size": len(THEME_VOCABULARY),
    }
    (outdir / "stats.json").write_text(json.dumps(stats, indent=2))

    print(json.dumps(stats, indent=2))
    print(f"\nwritten to {outdir.resolve()}/")


if __name__ == "__main__":
    main()
