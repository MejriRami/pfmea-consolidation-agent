"""
Grounding catalog for THEMER — one short description + 1-2 real example
failure modes per theme, embedded in the agent's instructions so it isn't
classifying from the bare theme name alone.

Examples are drawn ONLY from ground_truth/final_lines.jsonl rows that pass
pfmea_agents.ground_truth_alignment.check_alignment() (i.e. the row's own
failure_mode content actually matches what its dedup_key/theme describes).
That check exists because of a real defect: for ~51 of 192 final rows
(concentrated in the OP40/OP50 stripping/winding/tinning region), the
theme was assigned to a DIFFERENT row's content, not the row it's
attached to (see ground_truth_alignment.py for the full writeup). Pulling
examples from a desynced row would teach THEMER the wrong association —
e.g. row 60's theme is `winding_geometry` but its own failure_mode is a
stripping defect, not a winding one.

Seven themes have ZERO surviving example after that filter, because every
row carrying them in this ground truth snapshot happens to sit in the
desynced region: stripping_excessive_depth, stripping_position_dimension,
tinning_excess, tinning_insufficient, winding_excess_turns,
winding_geometry, wire_break_damage. Those get a hand-written description
only (marked `examples=[]` below) — precise enough from AIAG-VDA domain
knowledge and the theme name itself, but unverified against real data.
Re-derive them once Phase 0 fixes the AW alignment defect.

English glosses for the still-CJK-only ground truth entries are the
documented translations from retheme_decisions.py, not invented here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeEntry:
    description: str
    examples: tuple[str, ...] = ()


THEME_CATALOG: dict[str, ThemeEntry] = {
    "lcr_electrical_test": ThemeEntry(
        "LCR/electrical measurement result wrong, or the LCR/electrical test was not performed "
        "as required. Must genuinely be about an electrical measurement (inductance, resistance, "
        "LCR fixture) failing or being skipped. CAUTION: this ground truth's own historical usage "
        "of this theme is inconsistent — it has been applied to unrelated content (tin excess, "
        "turn count, coil damage, NOK escapes) across OP40-OP200, which is a documented ground-"
        "truth defect (see pfmea_agents.ground_truth_alignment), not a pattern to imitate. "
        "Classify strictly by content: no LCR/electrical measurement signal, no this theme.",
        (),  # deliberately no examples — every surviving one in the ground truth was noise
    ),
    "traceability_label": ThemeEntry(
        "Raw material or product traceability/labeling missing or incomplete.",
        ("Missing - incomplete raw material traceability / Labeling",),
    ),
    "core_ferrite_process": ThemeEntry(
        "General ferrite/core process-and-force bucket: use when a core pull-out-force, "
        "sensing-value, or raw-material-reference defect's CAUSE does NOT point to a specific "
        "glue-dispensing mechanism. If the cause clearly says missing glue, a dispensing "
        "process/parameter, or pressure, use glue_missing/glue_process/glue_pressure instead — "
        "several of those share nearly identical failure-mode wording ('Ferrite core pull out "
        "force under specifications') with this theme and can only be told apart by the CAUSE.",
        ("Ferrite core pull out force out of specifications", "Wrong raw material reference",
         "Under-curing / drying insufficient"),
    ),
    "consumable_expiry": ThemeEntry(
        "A process consumable (glue, tin, flux) used past its expiry/shelf life.",
        ("Expired glue", "Expired tin"),
    ),
    "glue_process": ThemeEntry(
        "A glue-dispensing PROCESS/EQUIPMENT/PARAMETER defect — the cause names a process or "
        "machine problem (wrong dispensing position/location, machine parameter, oven, conveyor "
        "speed), not simply missing glue (glue_missing) or pressure (glue_pressure). Can share "
        "the same failure-mode wording as glue_missing/core_ferrite_process ('Ferrite core pull "
        "out force...') — the cause is what disambiguates.",
        ("Ferrite core pull out force under specifications (cause: bad position of glue dispensing)",
         "Wrong dispensing location", "Conveyor speed too high or too low"),
    ),
    "glue_missing": ThemeEntry(
        "A defect whose CAUSE explicitly states glue is missing or insufficient in quantity. "
        "Failure-mode wording is frequently identical to glue_process/core_ferrite_process "
        "('Ferrite core pull out force under specifications', 'Inductance out of specification') "
        "— read failure_cause_en, not just failure_mode_en, to choose between them.",
        ("Ferrite core pull out force under specifications (cause: insufficient quantity of glue)",
         "Inductance out specification lower limit (cause: missing glue)"),
    ),
    "packaging_material": ThemeEntry(
        "Raw material or product packaging damaged, missing, or incorrect.",
        ("Damaged raw material packaging", "Damaged - Missing spool protection"),
    ),
    "angle_forming_nok": ThemeEntry(
        "Lead/pin forming angle or coil dimension out of specification.",
        ("Wrong Coil Dimension",),
    ),
    "flux_missing_wrong": ThemeEntry(
        "Flux absent, insufficient, contaminated, or wrong batch/reference used.",
        ("Missing coil turns",),  # weak example; theme is best identified from failure_cause ("flux") not this FM
    ),
    "wire_insulation_varnish": ThemeEntry(
        "Wire insulation/varnish damaged or broken.",
        ("Damaged wire insulation",),
    ),
    "core_dimension_damage": ThemeEntry(
        "The broader ferrite-core geometry/condition bucket: wrong size/dimension, or generic "
        "damage/cracking, mostly found at incoming inspection or positioning (not specifically "
        "tied to the OP150 push/pull-test process itself — that narrower fracture/detachment "
        "outcome is core_damage).",
        ("Damaged ferrite core", "Wrong core dimensions", "Ferrite/core damaged or cracked"),
    ),
    "lead_length_nok": ThemeEntry(
        "Lead/leg length out of specification (too short or too long).",
        ("Short/long leg length", "Short or long leg / leg length out of tolerance"),
    ),
    "wire_diameter_grade": ThemeEntry(
        "Copper wire diameter or grade wrong vs. specification.",
        ("Wrong copper wire diameter", "Wrong copper wire grad"),
    ),
    "tinning_quality": ThemeEntry(
        "General tinning appearance/quality complaint where the direction of the defect isn't "
        "the point (e.g. a vague 'bad aspect') — NOT a clear-cut 'missing/not enough tin' "
        "(that's tinning_insufficient) or 'too much tin' (tinning_excess).",
        ("Bad tinning aspect",),
    ),
    "stripping_nok": ThemeEntry(
        "Stripping (insulation/varnish removal) missing, incomplete, or damages the conductor.",
        ("Excessive stripping or conductor damage",),
    ),
    "curing_heating": ThemeEntry(
        "Curing/heating process temperature or result (e.g. Curie temperature) out of spec.",
        ("the Curie temperature NG", "Overheated coil"),
    ),
    "glue_pressure": ThemeEntry(
        "A glue defect whose cause EXPLICITLY names air/dispensing pressure (too high, too low, "
        "regulator/valve drift) — a specific subtype of glue_process; only use this when "
        "'pressure' (or an equivalent — air, valve, regulator) appears in the cause.",
        ("Glue dose variation due to pressure/valve drift", "The glue volume is higher than the "
         "standard volume (cause: excessive air pressure)"),
    ),
    "quantity_weight_packaging": ThemeEntry(
        "Wrong quantity or weight of parts/packaging.",
        ("Wrong quantity",),
    ),
    "raw_material_damage": ThemeEntry(
        "Physical damage to raw material or in-process parts (not packaging-specific).",
        ("Damaged materials", "Bad conditions"),
    ),
    "wire_straightening_feed": ThemeEntry(
        "Wire straightening or feed/guide mechanism defect.",
        ("Bad Straightening of Copper Wire", "Wire not correctly straightened"),
    ),
    "stripping_position_dimension": ThemeEntry(
        "Stripping is at the wrong position along the lead, or the stripped dimension is wrong "
        "(not a depth/excess-depth issue specifically).",
    ),
    "tin_bath_temperature": ThemeEntry(
        "Tin bath temperature or resulting tinning appearance (over/under-tinning) out of spec.",
        ("Bad tinning aspect (Over-tinning)", "Bad tinning aspect"),
    ),
    "wrong_material_reference": ThemeEntry(
        "Wrong material/part reference used, or products mixed up.",
        ("Wrong material number of Core", "Missing material on production line"),
    ),
    "push_force_low": ThemeEntry(
        "The MEASURED RESULT at the push/pull-force test operation itself reads below the lower "
        "specification limit. About the test reading, not the upstream process cause of a low "
        "force — a low pull-out force whose cause is missing glue, a dispensing process issue, or "
        "ferrite positioning belongs to glue_missing/glue_process/core_ferrite_process instead, "
        "even though their failure-mode wording can look identical to this theme's.",
        ("break force NG", "Pull out force under lower specification"),
    ),
    "winding_geometry": ThemeEntry(
        "Coil winding geometry defect: wrong length, diameter, or overall shape "
        "(not a turn-count issue specifically).",
    ),
    "winding_missing_turns": ThemeEntry(
        "Coil has fewer turns than specified, or the turn count is otherwise wrong.",
        ("Wrong number of turns",),
    ),
    "glue_excess_misplaced": ThemeEntry(
        "A physical EXCESS/contamination OUTCOME: too much glue applied, or glue visible where it "
        "shouldn't be. Classify by this visible outcome even if the root cause happens to be a "
        "pressure or process issue — use glue_pressure/glue_process only when the row is reporting "
        "the process parameter itself, not an excess-glue appearance result.",
        ("Coil contaminated with glue",),
    ),
    "stripping_excessive_depth": ThemeEntry(
        "Stripping/peeling depth exceeds specification (cuts too deep into or through the conductor).",
    ),
    "wrong_program_recipe": ThemeEntry(
        "Wrong machine program/recipe used, or an unauthorized parameter change.",
        ("Inductance out of specification not detected",),
    ),
    "core_missing": ThemeEntry(
        "Ferrite core missing from the assembly.",
        ("Missing core", "Missing ferrite/core"),
    ),
    "core_damage": ThemeEntry(
        "Ferrite core fractured or fell off/detached specifically as an outcome of the push/pull-"
        "test process (thrust pin/gauge equipment implicated in the cause) — a narrower, process-"
        "specific fracture outcome. Broader incoming/geometry damage is core_dimension_damage.",
        ("Fractured core", "Core fall down"),  # English half of bilingual cells; see module docstring
    ),
    "push_test_detection": ThemeEntry(
        "The push/pull-force TEST ITSELF fails to catch a real defect (false-OK result, weak "
        "sampling) — a detection-escape failure of the test method, distinct from push_force_low "
        "(where the force reading itself is correctly measured as out of spec).",
        ("False OK push test result", "Low push force not detected due to weak sampling/reaction"),
    ),
    "tinning_excess": ThemeEntry(
        "Too much solder/tin coating on a lead (thick legs, tin lumps).",
    ),
    "tinning_insufficient": ThemeEntry(
        "Too little solder/tin coating on a lead; tinning missing or incomplete.",
    ),
    "wire_break_damage": ThemeEntry(
        "Wire broken or otherwise physically damaged during handling/processing.",
    ),
    "core_position_orientation": ThemeEntry(
        "Ferrite core loose, out of position, or wrongly oriented (not missing, not fractured).",
        ("Loose Ferrite",),
    ),
    "cutting_burr_length": ThemeEntry(
        "Cutting operation leaves a burr, or cut length is wrong.",
        ("Cutted leg with burr",),
    ),
    "visual_control_escape": ThemeEntry(
        "A genuine detection escape: an NOK part passed a visual/control check undetected "
        "(the failure IS the missed detection, not the underlying physical defect).",
        ("Escape of NOK parts to Customer",),
    ),
    "winding_excess_turns": ThemeEntry(
        "Coil has more turns than specified.",
    ),
    "equipment_calibration_zero": ThemeEntry(
        "Measurement/test equipment (caliper, LCR meter) not calibrated or not zeroed, "
        "causing a misjudgment.",
        ("Misjudgment of Inductance", "Failure to inspect as required"),
    ),
    "material_magnetic_property": ThemeEntry(
        "Ferrite electromagnetic property out of spec at incoming: permeability, magnetic loss, "
        "saturation current, insertion loss. Distinct from core_dimension_damage (geometry) and "
        "raw_material_damage (physical damage).",
        ("permeability NG", "magnetic loss NG", "insertion loss too high"),
    ),
    "material_compliance": ThemeEntry(
        "Regulatory substance-limit failure (e.g. RoHS/REACH), not a quality/functional defect.",
        ("The harmful substances in the raw materials exceed the limit",),
    ),
}

UNCLASSIFIED_NOTE = (
    "If nothing above genuinely fits, output UNCLASSIFIED rather than forcing the closest match. "
    "A human reviews every UNCLASSIFIED row; a wrong forced match may never be looked at again."
)

assert set(THEME_CATALOG) == {
    "angle_forming_nok", "consumable_expiry", "core_damage", "core_dimension_damage",
    "core_ferrite_process", "core_missing", "core_position_orientation", "curing_heating",
    "cutting_burr_length", "equipment_calibration_zero", "flux_missing_wrong",
    "glue_excess_misplaced", "glue_missing", "glue_pressure", "glue_process",
    "lcr_electrical_test", "lead_length_nok", "material_compliance",
    "material_magnetic_property", "packaging_material", "push_force_low",
    "push_test_detection", "quantity_weight_packaging", "raw_material_damage",
    "stripping_excessive_depth", "stripping_nok", "stripping_position_dimension",
    "tin_bath_temperature", "tinning_excess", "tinning_insufficient", "tinning_quality",
    "traceability_label", "visual_control_escape", "winding_excess_turns",
    "winding_geometry", "winding_missing_turns", "wire_break_damage",
    "wire_diameter_grade", "wire_insulation_varnish", "wire_straightening_feed",
    "wrong_material_reference", "wrong_program_recipe",
}, "THEME_CATALOG must cover exactly the 42 frozen themes"
