"""
CLI: read a COMPLETED exports/Phase4_Cluster_Review.xlsx, resolve the
engineer's Merge / Keep Separate / Unsure decisions into the final
consolidated row set (via pfmea_agents.decision_ingestion, unmodified),
write those rows into the live standard_line table as drafts
(db.standard_line_writer.write_consolidated_rows), then approve every one
of them via db.standard_line_writer.approve_standard_line.

APPROVAL IS DELIBERATELY GATED BEHIND A REQUIRED --approved-by ARGUMENT:
this script exists so a named human reviewer can approve consolidated
content themselves, from their own terminal. Nothing else in this repo --
no MCP tool, no automated pipeline step -- can reach approve_standard_line
(spec §7: approval is human-only).

Loads .env for DATABASE_URL (see .env.example). --dry-run never touches
the database at all, so it works even without DATABASE_URL configured.

Usage:
    python approve_to_db.py --approved-by "Jane Doe"
    python approve_to_db.py --approved-by "Jane Doe" --dry-run
    python approve_to_db.py --approved-by "Jane Doe" --workbook path/to/other_review.xlsx
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

from pfmea_agents.decision_ingestion import GROUND_TRUTH_DIR, build_final_lines, read_review_workbook, resolve_decisions

DEFAULT_WORKBOOK_PATH = REPO_ROOT / "exports" / "Phase4_Cluster_Review.xlsx"


def _summarize_resolved(resolved: list) -> dict[str, int]:
    return {
        "baseline_rows": sum(1 for r in resolved if r.decision == "INCLUDE_BASELINE"),
        "kept_separate": sum(1 for r in resolved if r.decision == "INCLUDE_ENRICHMENT" and not r.flagged_uncertain),
        "flagged_uncertain": sum(1 for r in resolved if r.decision == "INCLUDE_ENRICHMENT" and r.flagged_uncertain),
        "merged_excluded": sum(1 for r in resolved if r.decision == "EXCLUDE_DUPLICATE"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approved-by", required=True, help="named human reviewer approving these rows")
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK_PATH), help="completed review workbook path")
    parser.add_argument("--ground-truth-dir", default=str(GROUND_TRUTH_DIR),
                         help="ground_truth dir used to reconstruct row content (default: this repo's ground_truth/)")
    parser.add_argument("--product-family", default=None,
                         help="defaults to db.standard_line_writer.DEFAULT_PRODUCT_FAMILY ('chokes')")
    parser.add_argument("--dry-run", action="store_true",
                         help="preview counts only -- writes and approves nothing")
    args = parser.parse_args(argv)

    workbook_path = Path(args.workbook)
    reviewed = read_review_workbook(workbook_path)
    resolved = resolve_decisions(reviewed)
    final_lines, exclusion_log = build_final_lines(resolved, ground_truth_dir=Path(args.ground_truth_dir))

    counts = _summarize_resolved(resolved)
    print(f"Reviewed workbook: {workbook_path}")
    print(f"  baseline rows:      {counts['baseline_rows']}")
    print(f"  kept separate:      {counts['kept_separate']}")
    print(f"  flagged uncertain:  {counts['flagged_uncertain']}")
    print(f"  merged (excluded):  {counts['merged_excluded']}")
    print(f"  total consolidated rows to write: {len(final_lines)}")

    if args.dry_run:
        print(
            f"\n[DRY RUN] Would write {len(final_lines)} row(s) to standard_line as draft, then approve "
            f"all of them as {args.approved_by!r}. Nothing was written or approved."
        )
        return 0

    # Imported here, not at module level, so --dry-run never requires a
    # working DATABASE_URL / DB dependency stack to run.
    from db.connection import get_session_factory
    from db.models import StandardLine
    from db.standard_line_writer import DEFAULT_PRODUCT_FAMILY, approve_standard_line, write_consolidated_rows

    product_family = args.product_family or DEFAULT_PRODUCT_FAMILY

    session_factory = get_session_factory()
    with session_factory() as session:
        write_summary = write_consolidated_rows(
            session, final_lines, actor=args.approved_by, product_family=product_family,
        )
        print(
            f"\nWrote to standard_line: {write_summary['inserted']} new version(s), "
            f"{write_summary['unchanged']} unchanged, {write_summary['superseded']} superseded."
        )

        approved_count = 0
        already_approved_count = 0
        for row in final_lines:
            line_key = f"{row['source']}:{row['row_id']}"
            record = (
                session.query(StandardLine)
                .filter(StandardLine.line_key == line_key)
                .order_by(StandardLine.version.desc())
                .first()
            )
            if record is None:
                continue
            if record.status == "approved":
                already_approved_count += 1
                continue
            approve_standard_line(session, record.id, approved_by=args.approved_by)
            approved_count += 1

    suffix = f" ({already_approved_count} were already approved)" if already_approved_count else ""
    print(f"Approved {approved_count} row(s) as {args.approved_by!r}{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
