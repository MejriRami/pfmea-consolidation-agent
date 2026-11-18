"""
STEP 2 — Answer key cleaning.
Re-theming of the 19 rows in 02_FINAL_PFMEA whose AW dedup key carried a
garbage theme (untranslated Chinese, or keyword-salad from a failed fallback).

Each decision is justified against the row's actual failure mode + cause.
Provisional: pending quality review. Config, not code — change freely.
"""

# ── Frozen theme vocabulary: 35 clean + 2 new = 37 ──────────────────────────
# Two themes added because no clean theme covered the case:
#   material_magnetic_property  — ferrite electromagnetic property NG at incoming.
#                                 Distinct from core_dimension_damage (geometry)
#                                 and raw_material_damage (physical damage).
#                                 Covers permeability / magnetic loss / saturation
#                                 current / insertion loss.
#   material_compliance         — regulatory substance limits (RoHS/REACH).
#                                 Not a quality defect; a compliance failure with
#                                 a different escalation path.

RETHEME = {
    # row : (old_theme, new_theme, justification)

    # ---- OP10 Incoming material ----------------------------------------
    25: ("assembly_compliant_damaged_disturbance", "raw_material_damage",
         "FM 'Damaged materials', cause 'improper stacking or storage'. "
         "Physical damage to RM in storage. Old theme referenced assembly, "
         "which is not this operation."),

    38: ("material_permeability_poor_raw", "material_magnetic_property",
         "FM 'permeability NG' from poor supplier material. Electromagnetic "
         "property failure, not geometry or physical damage. NEW THEME."),

    40: ("loss_magnetic_material_poor", "material_magnetic_property",
         "FM 'magnetic loss NG', same cause as row 38. Groups with 38/41/157."),

    41: ("current_material_poor_raw", "material_magnetic_property",
         "FM 'saturation current NG', same cause family. Groups with 38/40/157."),

    42: ("exceed_harmful_limit_material", "material_compliance",
         "FM 'harmful substances exceed the limit'. Regulatory compliance "
         "(RoHS/REACH), not a quality defect. NEW THEME."),

    51: ("check_correctly_did_material", "wrong_material_reference",
         "FM 'Missing material on production line', cause 'did not check the "
         "order of materials correctly'. Wrong/absent material reference. "
         "Old theme was verbatim word salad from the cause text."),

    # ---- OP40 Denoising / stripping / tinning --------------------------
    63: ("1.客户无法装配_2.影响客户焊接_arm_assemble", "stripping_nok",
         "FM 'Missing stripping', cause 'stripping station not functional'. "
         "Old theme was the untranslated failure EFFECT, not the mode."),

    68: ("damaged_followed_next_operation", "tinning_excess",
         "FM 'Too much coating tin (thick fuse legs or tin lump)', cause "
         "'deep peeling'. Excess tinning. Rows 68/69/71 share the FM and "
         "MUST share the theme — they differ only by 4M cause "
         "(Man / Machine / Material), which is correct AIAG-VDA practice."),

    69: ("broken_does_followed_inductor", "tinning_excess",
         "Same FM as 68. Cause: blunt/damaged peeling cutter (Machine)."),

    71: ("accepted_deformed_followed_next", "tinning_excess",
         "Same FM as 68. Cause: aged raw material tape (Material)."),

    # ---- OP60 Coil winding ---------------------------------------------
    102: ("visual_control_escape", "winding_geometry",
          "FM 'Wrong Coil length', cause 'WI does not define coil length "
          "target + tolerance'. Coil geometry. Old theme described a "
          "detection escape, which is not the failure mode."),

    # ---- OP70 Lead forming ---------------------------------------------
    124: ("accepted_damaged_does_followed", "raw_material_damage",
          "FM 'Damaged pieces are not accepted', cause 'operation not "
          "followed'. Physical damage to parts in process."),

    # ---- OP140 Heating / curing ----------------------------------------
    157: ("客户影响_性能下降_技术员_技术员开班未点检确认参数", "material_magnetic_property",
          "FM '插损损耗过高' = insertion loss too high. Electromagnetic "
          "performance failure. Old theme was untranslated effect+cause "
          "concatenated. Cause (technician did not verify start-up "
          "parameters) belongs in failure_cause, not the theme."),

    # ---- OP160 LCR electrical test -------------------------------------
    180: ("coil_coils_compliant_control", "wrong_material_reference",
          "FM 'Wrong Coil Part Number', cause 'mixture of coils at final "
          "control'. Wrong part reference. Pairs with row 201."),

    184: ("dimension_check", "equipment_calibration_zero",
          "FM 'Failure to inspect as required', cause 'calliper does not "
          "return to zero'. Measurement equipment calibration. The existing "
          "clean theme equipment_calibration_zero covers exactly this."),

    # ---- OP180 Final visual --------------------------------------------
    191: ("damaged_followed_next_operation", "raw_material_damage",
          "FM 'Damaged pieces', cause 'operation not followed'. Same pattern "
          "as row 124."),

    # ---- OP190 Packaging -----------------------------------------------
    196: ("1.无法组装_2.影响客户生产计划_after_assemble", "wrong_material_reference",
          "FM '混料' = mixing products, cause 'production line not cleaned "
          "after changeover'. Product mix-up. Old theme was the untranslated "
          "effect."),

    # ---- OP200 Containment & reaction ----------------------------------
    201: ("assembly_claim_coil_coils", "wrong_material_reference",
          "FM 'Wrong Coil Part Number', cause 'mixture of coils on final "
          "control workstation'. Identical FM to row 180 at the containment "
          "gate."),

    202: ("assembly_causing_claim_compliant", "visual_control_escape",
          "FM 'Escape of NOK parts to Customer', cause 'lack of operator "
          "concentration'. A genuine detection escape — this is the one row "
          "where visual_control_escape is correct (cf. row 102, where it "
          "was wrongly applied to a winding geometry defect)."),
}

# ── Frozen vocabulary ────────────────────────────────────────────────────
THEME_VOCABULARY = frozenset({
    "lcr_electrical_test", "traceability_label", "core_ferrite_process",
    "consumable_expiry", "glue_process", "glue_missing", "packaging_material",
    "angle_forming_nok", "flux_missing_wrong", "wire_insulation_varnish",
    "core_dimension_damage", "lead_length_nok", "wire_diameter_grade",
    "tinning_quality", "stripping_nok", "curing_heating", "glue_pressure",
    "quantity_weight_packaging", "raw_material_damage",
    "wire_straightening_feed", "stripping_position_dimension",
    "tin_bath_temperature", "wrong_material_reference", "push_force_low",
    "winding_geometry", "winding_missing_turns", "glue_excess_misplaced",
    "stripping_excessive_depth", "wrong_program_recipe", "core_missing",
    "core_damage", "push_test_detection", "tinning_excess",
    "tinning_insufficient", "wire_break_damage", "core_position_orientation",
    "cutting_burr_length", "visual_control_escape", "winding_excess_turns",
    "equipment_calibration_zero",
    "material_magnetic_property",   # NEW
    "material_compliance",          # NEW
})

UNCLASSIFIED = "UNCLASSIFIED"  # no match -> human queue, never invent

if __name__ == "__main__":
    bad = {t for _, t, _ in RETHEME.values()} - THEME_VOCABULARY
    assert not bad, f"theme outside frozen vocabulary: {bad}"
    print(f"rows re-themed          : {len(RETHEME)}")
    print(f"frozen vocabulary size  : {len(THEME_VOCABULARY)}")
    print("all re-themes are within the frozen vocabulary: OK")
