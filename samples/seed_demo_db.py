"""Seed a local demo database with FICTIONAL standard PFMEA content.

Everything here is invented for demonstration: a generic coil / choke
assembly process with plausible failure modes, causes and controls. No
customer, plant or production data is involved.

It goes through the project's real workflow on purpose:
  1. rows are written as DRAFT by db.standard_line_writer.write_consolidated_rows
  2. a named human reviewer approves them with approve_standard_line
     (the only code path allowed to set status='approved')
  3. a few rows are left as draft, and some operations get no content at
     all, so the demo shows that generation never uses unapproved rows and
     flags gaps instead of inventing content.

Usage:
    createdb pfmea_demo                       # any empty PostgreSQL database
    export DATABASE_URL=postgresql://user:pass@localhost:5432/pfmea_demo
    python -m samples.seed_demo_db
"""
from __future__ import annotations

import json
from pathlib import Path

from db.connection import get_engine, get_session_factory
from db.models import ApLookup, Base, StandardLine
from db.standard_line_writer import approve_standard_line, write_consolidated_rows
from rules.dedup_key import build_dedup_key

ROOT = Path(__file__).resolve().parent.parent
AP_TABLE = json.loads((ROOT / "ground_truth" / "ap_lookup.json").read_text(encoding="utf-8"))
PRODUCT_FAMILY = "chokes"
REVIEWER = "demo.quality.engineer"

# (operation, process_step, theme, failure_mode, effect, cause, prevention, detection, S, O, D)
LINES = [
    ("OP10", "OP10 — Incoming material & lot ID", "wrong_material_reference",
     "Wrong wire reference received", "Inductance out of tolerance at end of line",
     "Supplier label mix-up", "Supplier certificate checked per lot", "Barcode scan vs. purchase order", 7, 3, 3),
    ("OP10", "OP10 — Incoming material & lot ID", "raw_material_damage",
     "Damaged wire spool", "Wire break during winding", "Improper stacking in storage",
     "Storage rules + max stack height", "Visual inspection at reception", 5, 4, 5),
    ("OP10", "OP10 — Incoming material & lot ID", "material_compliance",
     "Material without RoHS/REACH declaration", "Customer compliance claim",
     "Declaration missing from supplier", "Approved supplier list with compliance clause", "Document check before release", 8, 2, 4),
    ("OP30", "OP30 — Winding setup", "wrong_program_recipe",
     "Wrong winding program loaded", "Wrong number of turns on the batch",
     "Operator selects wrong recipe", "Recipe linked to work order barcode", "First-off part LCR check", 7, 3, 3),
    ("OP30", "OP30 — Winding setup", "wire_straightening_feed",
     "Wire not straight at feed", "Irregular coil geometry",
     "Straightener rollers worn", "Preventive maintenance every 500 h", "Setup approval by team leader", 5, 4, 5),
    ("OP50", "OP50 — Tinning / solder bath", "tin_bath_temperature",
     "Solder bath temperature too low", "Poor solderability at customer",
     "Thermocouple drift", "Temperature controller with alarm", "Temperature logged every 2 h", 7, 4, 4),
    ("OP50", "OP50 — Tinning / solder bath", "tinning_insufficient",
     "Insufficient tinning length", "Open circuit after customer soldering",
     "Immersion depth incorrectly set", "Fixed depth stop on the fixture", "Visual check against limit sample", 8, 3, 5),
    ("OP50", "OP50 — Tinning / solder bath", "flux_missing_wrong",
     "Flux missing before tinning", "Non-wetted leads",
     "Flux reservoir empty", "Level sensor on flux tank", "100 % visual at OP180", 7, 3, 6),
    ("OP60", "OP60 — Coil winding", "winding_missing_turns",
     "Missing turns", "Inductance below specification",
     "Wire slip during winding", "Tension control on the winder", "100 % LCR test at OP160", 8, 3, 2),
    ("OP60", "OP60 — Coil winding", "wire_break_damage",
     "Wire insulation damaged during winding", "Short circuit between turns",
     "Sharp edge on the wire guide", "Guide inspection at each setup", "Hi-pot test at OP160", 9, 2, 3),
    ("OP110", "OP110 — Glue dispensing", "glue_missing",
     "Glue missing on the core", "Core detaches in the field",
     "Nozzle clogged", "Nozzle purge every 2 h", "Camera presence check", 8, 3, 3),
    ("OP110", "OP110 — Glue dispensing", "glue_excess_misplaced",
     "Excess glue on the leads", "Leads not solderable at customer",
     "Dosing volume set too high", "Dosing parameters locked in recipe", "Visual check at OP180", 6, 4, 5),
    ("OP110", "OP110 — Glue dispensing", "consumable_expiry",
     "Expired glue used", "Insufficient bond strength",
     "Pot-life not tracked", "Pot-life label with timer", "Pull test per shift at OP150", 7, 3, 4),
    ("OP140", "OP140 — Heating / curing", "curing_heating",
     "Glue not fully cured", "Core moves under vibration",
     "Oven dwell time too short", "Conveyor speed interlocked with recipe", "Oven profile check daily", 7, 3, 4),
    ("OP160", "OP160 — LCR electrical test", "lcr_electrical_test",
     "LCR tester out of calibration", "Bad parts shipped as good",
     "Calibration overdue", "Calibration plan with system lock", "Golden sample check every shift", 9, 2, 3),
    ("OP160", "OP160 — LCR electrical test", "equipment_calibration_zero",
     "Fixture zero not performed", "Measurement offset",
     "Step skipped at start of shift", "Start-up checklist", "Golden sample check every shift", 7, 3, 3),
    ("OP180", "OP180 — Final visual inspection", "visual_control_escape",
     "Cosmetic defect not detected", "Customer complaint",
     "Inspector fatigue", "Rotation every 2 h + limit samples", "Audit sampling of shipped lots", 5, 4, 6),
    ("OP190", "OP190 — Packaging / label", "traceability_label",
     "Wrong label on the box", "Lot cannot be traced",
     "Label printed for previous order", "Label printed from scanned work order", "Label scan before shipping", 6, 3, 2),
    ("OP190", "OP190 — Packaging / label", "quantity_weight_packaging",
     "Wrong quantity in the box", "Line stop at customer",
     "Manual counting error", "Counting by weighing scale", "Weight check at closing", 5, 3, 3),
]

# These stay in draft: a reviewer has not approved them yet, so generation must ignore them.
LEFT_AS_DRAFT = {"DEMO-014", "DEMO-017"}


def build_rows() -> list[dict]:
    rows = []
    for i, (op, step, theme, fm, effect, cause, prevention, detection, s, o, d) in enumerate(LINES, start=1):
        rows.append({
            "source": "DEMO_STANDARD",
            "row_id": f"DEMO-{i:03d}",
            "operation": op,
            "process_item": "Choke / coil assembly (fictional demo product)",
            "process_step": step,
            "work_element": None,
            "function_item": None,
            "function_step": None,
            "function_element": None,
            "failure_mode": fm,
            "failure_effect": effect,
            "failure_cause": cause,
            "prevention_control": prevention,
            "detection_control": detection,
            "severity": s,
            "occurrence": o,
            "detection": d,
            "ap": AP_TABLE[f"{s}-{o}-{d}"],
            "special_char": None,
            "theme": theme,
            "dedup_key": build_dedup_key(op, theme, fm),
            "justification": "Fictional demo content.",
        })
    return rows


def main() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    session_factory = get_session_factory()
    with session_factory() as session:
        if session.query(ApLookup).count() == 0:
            for key, ap in AP_TABLE.items():
                s, o, d = (int(x) for x in key.split("-"))
                session.add(ApLookup(severity=s, occurrence=o, detection=d, ap=ap))
            session.commit()

        result = write_consolidated_rows(
            session, build_rows(), actor="seed_demo_db", product_family=PRODUCT_FAMILY,
            change_reason="Fictional demo content",
        )
        drafts = session.query(StandardLine).filter(StandardLine.status == "draft").all()
        approved = 0
        for line in drafts:
            if line.line_key.split(":", 1)[1] in LEFT_AS_DRAFT:
                continue
            approve_standard_line(session, line.id, approved_by=REVIEWER, reason="Demo approval")
            approved += 1
        session.commit()
    print(f"standard_line: {result}; approved by {REVIEWER}: {approved}; left as draft: {len(LEFT_AS_DRAFT)}")


if __name__ == "__main__":
    main()
