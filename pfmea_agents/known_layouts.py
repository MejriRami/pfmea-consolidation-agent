"""
Ground truth for the MAPPER eval — the Phase 2 analogue of Phase 0's
ground_truth/ directory. Column maps below were derived by directly opening
each of the 5 known files with openpyxl and reading their actual header
rows; they are not copied from the spec (which gets header rows wrong for
two of the five — see the note on SITE_B/SITE_C below).

`file_pattern` / `sheet_pattern` are matched case-insensitively against the
filename stem / raw sheet name, so this survives the spacing/underscore
inconsistencies between the spec's file names and what's actually on disk
(e.g. spec: "PFMEA_CustomerA_AIAG_VDA-2026.xlsx", disk: "PFMEA Customer A AIAG
VDA-2026.xlsx").

`columns` only records the mandatory fields (spec §5.1 eval: "exact
column-mapping accuracy... 100% on mandatory fields for known layouts").
Optional-field ground truth wasn't hardened into a fixture here — grading
those precisely would require pinning down genuinely ambiguous calls (e.g.
SITE_B/SITE_C's "Requirement / Specification" column has no clean home in the
canonical vocabulary) that don't affect the pass/fail bar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .mapper import MANDATORY_FIELDS


@dataclass(frozen=True)
class KnownLayout:
    key: str
    file_pattern: re.Pattern
    sheet_pattern: re.Pattern
    header_row: int
    columns: dict[str, str]  # mandatory-field -> column letter, ground truth
    rating_method: str
    languages: list[str] = field(default_factory=list)


KNOWN_LAYOUTS: list[KnownLayout] = [
    KnownLayout(
        key="MASTER_REF",
        # Tightened past "master.*pilot1": Downloads folders regularly
        # accumulate re-downloaded copies of this file ("... Pilot1 (2).xlsx",
        # "... Pilot1 (2) (1).xlsx") that would otherwise tie for the match.
        # "dashboard" pins it to the exact file named in spec §0 / used to
        # build ground_truth/ in Phase 0.
        file_pattern=re.compile(r"master.*pilot1.*dashboard", re.I),
        sheet_pattern=re.compile(r"^02_FINAL_PFMEA$"),
        header_row=9,
        columns={
            "process_step": "D",
            "failure_mode": "I",
            "failure_effect": "J",
            "severity": "K",
            "failure_cause": "L",
            "occurrence": "N",
            "detection": "P",
        },
        rating_method="AP",
        languages=["en"],
    ),
    KnownLayout(
        key="CUSTOMER_A_AIAG_VDA",
        # Excludes "master"/"pilot1": the master workbook's own filename
        # also contains "CustomerA_AIAG_VDA" as a substring, and Downloads
        # folders tend to have re-downloaded copies of it too.
        file_pattern=re.compile(r"^(?!.*master)(?!.*pilot1).*customer.?a.*aiag.*vda", re.I),
        sheet_pattern=re.compile(r"^PFMEA $"),  # exact incl. trailing space; excludes 'PFMEA  (2)' / 'PFMEA  history '
        header_row=9,
        columns={
            "process_step": "D",
            "failure_mode": "I",
            "failure_effect": "J",
            "severity": "K",
            "failure_cause": "L",
            "occurrence": "N",
            "detection": "P",
        },
        rating_method="AP",
        languages=["en"],
    ),
    KnownLayout(
        # KNOWN SPEC DEFECT: spec §4 says header row "~13". The actual
        # workbook has a severity/occurrence/detection legend occupying
        # rows 1-26; the real header ("Operation #", "Failure Type", ...)
        # is at row 27. Confirmed by direct inspection.
        key="SITE_B",
        file_pattern=re.compile(r"dya", re.I),
        sheet_pattern=re.compile(r"^F-ING-004-H$"),
        header_row=27,
        columns={
            "process_step": "B",
            "failure_mode": "D",
            "failure_effect": "E",
            "severity": "F",
            "failure_cause": "H",
            "occurrence": "I",
            "detection": "M",
        },
        rating_method="RPN",
        languages=["en", "es"],
    ),
    KnownLayout(
        # Same template as SITE_B (F-ING-004-H); same header-row defect.
        key="SITE_C",
        file_pattern=re.compile(r"nic", re.I),
        sheet_pattern=re.compile(r"^F-ING-004-H$"),
        header_row=27,
        columns={
            "process_step": "B",
            "failure_mode": "D",
            "failure_effect": "E",
            "severity": "F",
            "failure_cause": "H",
            "occurrence": "I",
            "detection": "M",
        },
        rating_method="RPN",
        languages=["en", "es"],
    ),
    KnownLayout(
        key="SITE_D_LINE10",
        # Requires "pfmea" as well as "esp10": the companion process-flow-
        # chart file for the same part is literally named "PFC CUSTA
        # LINE10...xlsx" — a different document (spec §4/§10: PFC is out of
        # scope for the pilot), not just a naming variant of this one.
        file_pattern=re.compile(r"pfmea.*esp10", re.I),
        sheet_pattern=re.compile(r"^PFMEA%20CUSTA%20LINE10%200000001$"),  # excludes the '(2)' variant
        header_row=11,
        columns={
            "process_step": "B",
            # NOTE the two-severity trap (spec §4): K = "Severity (S) of All
            # FE" (rollup, must NOT be picked), M = "Severity (S) of FE"
            # (per-effect, correct pick).
            "failure_mode": "J",
            "failure_effect": "L",
            "severity": "M",
            "failure_cause": "N",
            "occurrence": "R",
            "detection": "T",
        },
        rating_method="AP",
        languages=["zh", "en"],
    ),
]

assert {"MASTER_REF", "CUSTOMER_A_AIAG_VDA", "SITE_B", "SITE_C", "SITE_D_LINE10"} == {
    layout.key for layout in KNOWN_LAYOUTS
}
for _layout in KNOWN_LAYOUTS:
    _bad = set(_layout.columns) - set(MANDATORY_FIELDS)
    assert not _bad, f"{_layout.key}: columns keys must be MANDATORY_FIELDS, got extra {_bad}"
    _missing = set(MANDATORY_FIELDS) - set(_layout.columns)
    assert not _missing, f"{_layout.key}: ground truth missing mandatory fields {_missing}"
