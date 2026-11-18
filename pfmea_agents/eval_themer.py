"""
Phase 3 replay benchmark (spec §5.3 / §8): "THEMER — Done when: Theme
agreement >= 0.90 vs. clean ground truth."

Runs the live NORMALIZER -> THEMER pipeline (requires OPENAI_API_KEY)
against ground_truth/final_lines.jsonl and reports exact agreement with the
stored `theme` field.

SCORING SCOPE — READ BEFORE INTERPRETING THE NUMBER:
Of 192 final rows, 2 have no usable dedup key (documented Phase 0 orphans)
and 51 have a `theme` that — per pfmea_agents.ground_truth_alignment — was
assigned to a DIFFERENT row's content than the row's own failure_mode (a
ground-truth defect discovered while building this eval, not a THEMER
problem). Scoring THEMER against those 51 would measure agreement with the
wrong answer, not a real disagreement. The pass/fail bar (>=0.90) is
computed ONLY over the 139 alignment-verified rows; the excluded rows are
still run and reported separately, for visibility, not for the bar.

CONCURRENCY: rows are independent of each other, so they run concurrently
(bounded by --concurrency) instead of one at a time. A fully sequential run
(up to 3 chained API calls per row x ~190 rows against a reasoning model)
was impractically slow in practice — tens of minutes for a partial run.

Usage:
    python -m pfmea_agents.eval_themer
    python -m pfmea_agents.eval_themer --limit 40             # cheaper smoke test
    python -m pfmea_agents.eval_themer --concurrency 4         # gentler on rate limits
"""
from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter

from .ground_truth_alignment import check_alignment
from .normalizer import is_plain_ascii, run_normalizer_async
from .themer import ThemerInput, run_themer_async

DEFAULT_CONCURRENCY = 8


def _check_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. NORMALIZER/THEMER run on the OpenAI Agents "
            "SDK (spec section 2, Layer 2) and cannot be evaluated without it.\n"
            "Set it and re-run:\n"
            '  $env:OPENAI_API_KEY = "sk-..."   (PowerShell)\n'
            "  python -m pfmea_agents.eval_themer"
        )


async def _to_english_async(text: str, *, model: str) -> str:
    """NORMALIZER is skipped for pure-ASCII text: translating English to
    English is a wasted API call, and the eval's target metric is theme
    agreement, not NORMALIZER's own typo-correction quality (which has no
    ground truth here anyway — see PHASE_0_RESULTS.md item 6)."""
    if is_plain_ascii(text):
        return text
    return (await run_normalizer_async(text, model=model)).english


async def eval_one_async(row: dict, *, model: str, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        failure_mode_en, failure_cause_en = await asyncio.gather(
            _to_english_async(row["failure_mode"], model=model),
            _to_english_async(row["failure_cause"] or "", model=model),
        )
        themer_input = ThemerInput(
            operation=row["operation"],
            process_step=row["process_step"] or "",
            failure_mode_en=failure_mode_en,
            failure_cause_en=failure_cause_en,
        )
        output = await run_themer_async(themer_input, model=model)

    return {
        "excel_row": row["excel_row"],
        "expected": row["theme"],
        "got": output.theme,
        "match": output.theme == row["theme"],
        "confidence": output.confidence,
    }


async def eval_many_async(rows: list[dict], *, model: str, concurrency: int) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*(eval_one_async(r, model=model, semaphore=semaphore) for r in rows))


async def _run(args: argparse.Namespace) -> int:
    report = check_alignment()
    scored_rows = report.aligned[: args.limit] if args.limit else report.aligned
    # --limit also caps the informational desynced set, proportionally —
    # a "smoke test" that still silently runs all 51 desynced rows isn't
    # actually cheap.
    desynced_rows = report.desynced[: args.limit] if args.limit else report.desynced

    print(f"scoring {len(scored_rows)}/{len(report.aligned)} alignment-verified rows "
          f"(excluding {len(report.desynced)} ground-truth-desynced + "
          f"{len(report.no_dedup_key)} no-dedup-key rows from the pass/fail bar), "
          f"concurrency={args.concurrency}\n")

    results = await eval_many_async(scored_rows, model=args.model, concurrency=args.concurrency)
    n_correct = sum(1 for r in results if r["match"])
    accuracy = n_correct / len(results) if results else 0.0

    mismatches = [r for r in results if not r["match"]]
    confusion = Counter((r["expected"], r["got"]) for r in mismatches)

    print(f"=== agreement on alignment-verified subset: {accuracy:.1%} "
          f"({n_correct}/{len(results)}) {'PASS' if accuracy >= 0.90 else 'FAIL'} (target >= 90%) ===\n")

    if mismatches:
        print("top mismatches (expected -> got):")
        for (expected, got), count in confusion.most_common(15):
            print(f"  {count:>3}x  {expected!r} -> {got!r}")

    # Informational only — never counted toward the pass/fail bar.
    if desynced_rows:
        print(f"\n--- informational: {len(desynced_rows)} ground-truth-desynced rows "
              f"(not scored; see ground_truth_alignment.py) ---")
        desync_results = await eval_many_async(desynced_rows, model=args.model, concurrency=args.concurrency)
        desync_agree = sum(1 for r in desync_results if r["match"])
        print(f"  THEMER happened to match the (likely-wrong) stored theme on "
              f"{desync_agree}/{len(desync_results)} of these — low agreement here is "
              f"EXPECTED and does not indicate a THEMER problem.")

    return 0 if accuracy >= 0.90 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--limit", type=int, default=None,
                         help="cap the number of alignment-verified rows scored (cost control); "
                              "also proportionally caps the informational desynced set")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                         help="max rows in flight at once (lower this if you hit rate limits)")
    args = parser.parse_args(argv)

    _check_api_key()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
