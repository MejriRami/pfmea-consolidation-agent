"""
Phase 2 replay benchmark (spec §8): "MAPPER agent — Done when: 100%
mandatory-field mapping on the 5 known files."

Runs the live MAPPER agent (requires OPENAI_API_KEY) against each of the 5
known layouts and reports exact column-mapping accuracy on the mandatory
fields, per file and overall.

Usage:
    python -m pfmea_agents.eval_mapper --source-dir "C:\\path\\to\\workbooks"
"""
from __future__ import annotations

import argparse
import os
import sys

from .grid import render_sheet_grid
from .known_layouts import KNOWN_LAYOUTS
from .mapper import MANDATORY_FIELDS, run_mapper_on_grid
from .resolve import ResolutionError, resolve_known_files


def _check_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. The MAPPER agent runs on the OpenAI Agents "
            "SDK (spec section 2, Layer 2) and cannot be evaluated without it.\n"
            "Set it and re-run, e.g.:\n"
            '  $env:OPENAI_API_KEY = "sk-..."   (PowerShell)\n'
            "  python -m pfmea_agents.eval_mapper --source-dir <dir>"
        )


def eval_one(key: str, resolved, *, model: str, max_rows: int) -> dict:
    grid = render_sheet_grid(resolved.path, resolved.sheet_name, max_rows=max_rows)
    output = run_mapper_on_grid(grid, model=model)

    expected = resolved.layout.columns  # mandatory field -> column letter, ground truth
    field_results = {}
    for f in MANDATORY_FIELDS:
        exp = expected.get(f)
        got = output.columns.get(f)
        field_results[f] = {"expected": exp, "got": got, "match": exp == got}

    n_correct = sum(1 for r in field_results.values() if r["match"])
    return {
        "key": key,
        "file": resolved.path.name,
        "sheet": resolved.sheet_name,
        "header_row_expected": resolved.layout.header_row,
        "header_row_got": output.header_row,
        "header_row_match": output.header_row == resolved.layout.header_row,
        "rating_method_expected": resolved.layout.rating_method,
        "rating_method_got": output.rating_method,
        "confidence": output.confidence,
        "field_results": field_results,
        "mandatory_accuracy": n_correct / len(MANDATORY_FIELDS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, help="folder containing the 5 known .xlsx files")
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--max-rows", type=int, default=40,
                         help="rows of the sheet to show MAPPER (spec says 20; "
                              "SITE_B/SITE_C need 40 — see grid.py docstring)")
    args = parser.parse_args(argv)

    _check_api_key()

    try:
        resolved_map = resolve_known_files(args.source_dir)
    except ResolutionError as exc:
        print(f"FAILED to resolve known files:\n{exc}", file=sys.stderr)
        return 2

    results = []
    for layout in KNOWN_LAYOUTS:
        resolved = resolved_map[layout.key]
        print(f"--- {layout.key} ({resolved.path.name} :: {resolved.sheet_name}) ---")
        r = eval_one(layout.key, resolved, model=args.model, max_rows=args.max_rows)
        results.append(r)

        print(f"  header_row: expected={r['header_row_expected']} got={r['header_row_got']} "
              f"{'OK' if r['header_row_match'] else 'MISMATCH'}")
        print(f"  rating_method: expected={r['rating_method_expected']} got={r['rating_method_got']}")
        print(f"  confidence: {r['confidence']:.2f}")
        for field_name, fr in r["field_results"].items():
            mark = "OK" if fr["match"] else "MISS"
            print(f"    [{mark}] {field_name}: expected={fr['expected']!r} got={fr['got']!r}")
        print(f"  mandatory field accuracy: {r['mandatory_accuracy']:.0%}\n")

    overall = sum(r["mandatory_accuracy"] for r in results) / len(results)
    all_perfect = all(r["mandatory_accuracy"] == 1.0 for r in results)
    print(f"=== overall mandatory-field accuracy: {overall:.1%} "
          f"({'PASS — 100% on every known file' if all_perfect else 'FAIL'}) ===")

    return 0 if all_perfect else 1


if __name__ == "__main__":
    raise SystemExit(main())
