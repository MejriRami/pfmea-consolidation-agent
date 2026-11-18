"""
Loaders for ap_lookup (from ground_truth/ap_lookup.json) and raw_line
(from ground_truth/raw_lines.jsonl). Each loader clears only ITS OWN
table before reloading (idempotent re-run), never anything else in the
database.

raw_line.source_file_id is NOT NULL, so raw_lines can't load until
source_file has one row per distinct `source` value in raw_lines.jsonl —
load_source_files() does that first, using db/source_registry.py for the
metadata ground_truth/raw_lines.jsonl doesn't itself carry (filename,
plant, customer, rating_method).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from .models import ApLookup, RawLine, SourceFile
from .source_registry import SOURCE_FILE_REGISTRY

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
GROUPED_SHEET_NAME = "01_ALL_SOURCES_GROUPED"
IMPORTED_BY = "phase5_loader"


def load_ap_lookup(session: Session, path: Path = GROUND_TRUTH_DIR / "ap_lookup.json") -> int:
    data = json.loads(path.read_text(encoding="utf-8"))

    session.query(ApLookup).delete()

    rows = []
    for key, ap in data.items():
        s, o, d = (int(x) for x in key.split("-"))
        rows.append(ApLookup(severity=s, occurrence=o, detection=d, ap=str(ap).strip()))
    session.bulk_save_objects(rows)
    session.commit()
    return len(rows)


def load_source_files(session: Session, raw_lines: list[dict]) -> dict[str, int]:
    """One source_file row per distinct `source` value actually present in
    raw_lines.jsonl. row_count comes from the data itself, not the registry.

    Upserts by source_id rather than delete-then-recreate: raw_line rows
    reference source_file.id via a NOT NULL FK, so deleting all source_file
    rows on a re-run would violate that constraint the moment any raw_line
    rows already exist (and recreating them would churn the ids anyway,
    for no benefit — source_file identity should be stable across reloads).
    """
    counts = Counter(r["source"] for r in raw_lines)

    existing = {sf.source_id: sf for sf in session.query(SourceFile).all()}

    source_file_ids: dict[str, int] = {}
    for source_id, row_count in counts.items():
        meta = SOURCE_FILE_REGISTRY.get(source_id)
        if meta is None:
            raise KeyError(
                f"no SOURCE_FILE_REGISTRY entry for source {source_id!r} — "
                f"add one in db/source_registry.py before loading"
            )

        if source_id in existing:
            record = existing[source_id]
            record.filename = meta.filename
            record.sheet_name = meta.sheet_name
            record.plant = meta.plant
            record.customer = meta.customer
            record.rating_method = meta.rating_method
            record.row_count = row_count
            session.flush()
            source_file_ids[source_id] = record.id
            continue

        record = SourceFile(
            source_id=source_id,
            filename=meta.filename,
            sheet_name=meta.sheet_name,
            plant=meta.plant,
            customer=meta.customer,
            color_code=None,  # see db/source_registry.py: no verified per-source color mapping
            imported_by=IMPORTED_BY,
            rating_method=meta.rating_method,
            row_count=row_count,
        )
        session.add(record)
        session.flush()  # populate record.id without a full commit yet
        source_file_ids[source_id] = record.id

    session.commit()
    return source_file_ids


def load_raw_lines(
    session: Session,
    path: Path = GROUND_TRUTH_DIR / "raw_lines.jsonl",
    *,
    source_file_ids: dict[str, int] | None = None,
) -> int:
    with path.open(encoding="utf-8") as fh:
        raw_lines = [json.loads(line) for line in fh]

    if source_file_ids is None:
        source_file_ids = load_source_files(session, raw_lines)

    session.query(RawLine).delete()

    rows = []
    for record in raw_lines:
        rows.append(RawLine(
            source_file_id=source_file_ids[record["source"]],
            source_row_ref=f"{GROUPED_SHEET_NAME}!R{record['excel_row']}",
            source_row_id=record.get("row_id"),
            raw_payload=record,  # every original field, verbatim, as extracted by Phase 0
            original_wording=None,  # NORMALIZER hasn't run over these rows yet (Phase 3 scope)
        ))
    session.bulk_save_objects(rows)
    session.commit()
    return len(rows)
