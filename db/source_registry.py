"""
Per-source metadata for populating source_file rows before loading
raw_line (raw_line.source_file_id is NOT NULL, so this has to exist
first). ground_truth/raw_lines.jsonl only carries `source` (e.g.
"MASTER_REF") per row, not filename/sheet/plant — those come from direct
inspection of the actual source files during this project (Phase 2/4
investigation), cited per field below. Anything not directly evidenced is
"Unknown" rather than guessed — never a fabricated specific.

rating_method is derived from the data itself (see
scripts/derive_rating_method.py-equivalent reasoning): every MASTER_REF,
SITE_D_PFMEA, and Gemba row has severity/occurrence/detection populated, and
MASTER_REF/Gemba have `ap` populated on 100% of rows (native AP source per
spec §4's table); SITE_D_PFMEA has `ap` on only 50% of rows despite 100%
S/O/D coverage, consistent with spec §4 characterizing it as AP-rated
with partial per-row entry, not RPN. SITE_B/SITE_C are RPN-native per
spec §4 ("RPN files (SITE_B/SITE_C) have no AP... recompute by lookup") even
though ~58% show a recomputed `ap` already. SITE_D_PFC rows have 0% `ap` and
are process-flow evidence only (spec §3.1/§4) — never PFMEA-rated at all.
"""
from __future__ import annotations

from dataclasses import dataclass

UNKNOWN = "Unknown"


@dataclass(frozen=True)
class SourceFileMeta:
    filename: str
    sheet_name: str
    plant: str
    customer: str | None
    rating_method: str


SOURCE_FILE_REGISTRY: dict[str, SourceFileMeta] = {
    "MASTER_REF": SourceFileMeta(
        # Filename/plant/customer directly observed: 01_ALL_SOURCES_GROUPED!AH10
        # ('PFMEA_Master Customer A AIAG VDA-2026.xlsx') and the master's own cover
        # sheet (C4/E4='Plant Location: Plant 1', C5/E5='Customer Name: Customer A').
        filename="PFMEA_Master_CustomerA_AIAG_VDA_2026_Pilot1_Dashboard_PFC_Updated.xlsx",
        sheet_name="01_ALL_SOURCES_GROUPED",
        plant="Plant 1, Tunisia",
        customer="Customer A",
        rating_method="AP",
    ),
    "SITE_D_PFMEA": SourceFileMeta(
        # Filename directly confirmed (Phase 2 file resolution against the
        # actual workbook). Plant from the SITE_D_ source-id prefix (spec's own
        # naming convention); customer from the filename itself ("CUSTA").
        filename="PFMEA CUSTA LINE10 PN-0000001& PN-0000002.xlsx",
        sheet_name="01_ALL_SOURCES_GROUPED",
        plant="Site D",
        customer="Customer A",
        rating_method="AP",
    ),
    "SITE_B": SourceFileMeta(
        # Filename directly confirmed. Plant directly observed in the file's
        # own header (L1='ASSYMEX Monterrey S.A. de C.V.'). Customer not
        # established anywhere in ground_truth/ or the direct file dumps.
        filename="FMEA-IND-SITE_B-001 rev  C.xlsx",
        sheet_name="F-ING-004-H",
        plant="Monterrey, Site M",
        customer=None,
        rating_method="RPN",
    ),
    "SITE_C": SourceFileMeta(
        filename="FMEA-IND-SITE_C-001-Rev F.xlsx",
        sheet_name="F-ING-004-H",
        plant="Monterrey, Site M",
        customer=None,
        rating_method="RPN",
    ),
    "Gemba + AI Suggestions": SourceFileMeta(
        # Not a single source spreadsheet -- spec describes this as compiled
        # shop-floor observations, not one of the 5 known files. Plant is
        # genuinely unverified (paired with the Tunisia baseline in the
        # data, but that's an inference, not direct evidence) -- left Unknown
        # rather than guessed.
        filename="Gemba shop-floor observations (compiled, no single source file)",
        sheet_name="01_ALL_SOURCES_GROUPED",
        plant=UNKNOWN,
        customer=None,
        rating_method="AP",
    ),
    "SITE_D_PFC": SourceFileMeta(
        # Filename directly confirmed as the companion process-flow file
        # (distinct from SITE_D_PFMEA's file) found during Phase 2 resolution.
        # Never becomes a PFMEA row (spec §3.1/§3.2) -- rating_method N/A.
        filename="PFC CUSTA LINE10 PN-0000001& PN-0000002.xlsx",
        sheet_name=UNKNOWN,
        plant="Site D",
        customer="Customer A",
        rating_method="N/A",
    ),
}
