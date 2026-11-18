#!/usr/bin/env python3
"""
verify_export.py - automated gap check for a generated PFMEA export.

Usage:
    python verify_export.py <generated_export.xlsx> <master_workbook.xlsx>

What it does: finds the Final PFMEA sheet by its header text (not by tab name),
maps columns by header wording, then runs deterministic checks tied to the
GAP_1.xlsx findings. Each check prints PASS / FAIL / REVIEW / SKIP.
  PASS    deterministic check found nothing wrong
  FAIL    deterministic check found the defect
  REVIEW  heuristic hit; a person must look at the examples
  SKIP    cannot be checked automatically (the reason is printed)
"""
import re, sys, datetime, warnings
from collections import Counter
import openpyxl
warnings.filterwarnings("ignore")

def norm(x):
    return re.sub(r"\s+", " ", str(x)).strip().lower() if x is not None else ""

def txt(x):
    return "" if x is None else str(x).strip()

def blank(x):
    return txt(x) == ""

results = []
def report(gid, name, status, detail="", examples=None):
    results.append((gid, name, status, detail, examples or []))

# ---------------------------------------------------------------- load
if len(sys.argv) < 3:
    print(__doc__); sys.exit(1)
exp_path, master_path = sys.argv[1], sys.argv[2]
wb = openpyxl.load_workbook(exp_path, data_only=True)
master = openpyxl.load_workbook(master_path, data_only=True)

# ---------------------------------------------------------------- find Final PFMEA sheet + header
fp_ws, hdr_row = None, None
for ws in wb.worksheets:
    for r in range(1, 25):
        row = [norm(c) for c in next(ws.iter_rows(min_row=r, max_row=r, values_only=True))]
        if any("failure mode (fm)" in c for c in row) and any("severity (s) of fe" in c for c in row):
            fp_ws, hdr_row = ws, r
            break
    if fp_ws: break
if not fp_ws:
    print("Could not find a sheet containing the AIAG-VDA header (Failure Mode (FM) + Severity (S) of FE).")
    sys.exit(2)

hdr = [norm(c) for c in next(fp_ws.iter_rows(min_row=hdr_row, max_row=hdr_row, values_only=True))]

def col(*pats, exact=False, after=None):
    for i, h in enumerate(hdr):
        if after is not None and i <= after: continue
        for p in pats:
            if (exact and h == p) or (not exact and p in h):
                return i
    return None

C = dict(
    sub=col("process step"), we=col("process work element"), fm=col("failure mode (fm)"),
    fe=col("failure effect (fe)"), s=col("severity (s) of fe"), cause=col("failure cause (fc)"),
    pc=col("current prevention control"), o=col("occurence (o) of fc", "occurrence (o) of fc"),
    dc=col("current detection control"), d=col("detection (d) of fc/fm"), ap=col("pfmea ap"),
    sc=col("special characteristics"), pa=col("prevention action"), da=col("detection action"),
    owner=col("responsible"), tdate=col("target"), status=col("status", exact=True),
    taken=col("action taken"), pfc=col("pfc_link", "pfc link"), qg=col("qg_link", "qg link"),
    srcplant=col("source plant"), srcfile=col("source fmea file", "source file"),
    origmeth=col("original rating method"), dupkey=col("duplicate_elimination_key"),
    refs=col("source_row_refs", "source sheet/row", "selected_source_rows"),
)
rows = []
for r in fp_ws.iter_rows(min_row=hdr_row + 1, values_only=True):
    r = list(r) + [None] * 80
    if C["sub"] is not None and not blank(r[C["sub"]]):
        rows.append(r)

def cell(r, key):
    i = C.get(key)
    return None if i is None else r[i]

def need(keys):
    miss = [k for k in keys if C.get(k) is None]
    return miss

def skip_missing(gid, name, keys):
    m = need(keys)
    if m:
        report(gid, name, "SKIP", "column not found in export header: " + ", ".join(m))
        return True
    return False

# ---------------------------------------------------------------- structure
names = [ws.title for ws in wb.worksheets]
expected = ["cover", "final", "ap", "history", "dashboard", "top"]
ok = len(names) == 6 and all(e in norm(n) for e, n in zip(expected, names))
report("STRUCT-1", "Exactly 6 sheets in order: Cover, Final PFMEA, AP, History, Dashboard, Top 10",
       "PASS" if ok else "FAIL", f"sheets found: {names}")

all_text = []
for ws in wb.worksheets:
    for row in ws.iter_rows(values_only=True):
        for c in row:
            if isinstance(c, str): all_text.append(c)
joined = "\n".join(all_text)
has_draft = ("Draft / Not Approved Standard" in joined) or ("Draft — AI-generated" in joined)
report("G-027", "Draft marking present in the file", "PASS" if has_draft else "FAIL")
report("G-001", "No 'Confirmed Route' wording anywhere",
       "FAIL" if re.search(r"confirmed route", joined, re.I) else "PASS")

# ---------------------------------------------------------------- cover sheet checks
def find_label_value(workbook, label_pat, sheet_filter=None):
    """return text of the first non-empty cell to the right of a label cell"""
    for ws in workbook.worksheets:
        if sheet_filter and sheet_filter not in norm(ws.title): continue
        for row in ws.iter_rows():
            for i, c in enumerate(row):
                if isinstance(c.value, str) and re.search(label_pat, c.value, re.I):
                    for nxt in row[i + 1:]:
                        if not blank(nxt.value): return nxt.value
    return None

mcover = master["FMEA COVER SHEET"] if "FMEA COVER SHEET" in master.sheetnames else None
def mval(label):
    if not mcover: return None
    for row in mcover.iter_rows():
        for i, c in enumerate(row):
            if isinstance(c.value, str) and norm(c.value) == label:
                for nxt in row[i + 1:]:
                    if not blank(nxt.value): return nxt.value
    return None

def dstr(v):
    return v.strftime("%Y-%m-%d") if isinstance(v, (datetime.datetime, datetime.date)) else txt(v)

ei, el = find_label_value(wb, r"date initial edition"), find_label_value(wb, r"date last change")
mi, ml = mval("date initial edition"), mval("date last change")
if ei is None or el is None:
    report("G-004", "Document-control dates preserved from master", "SKIP", "cover date labels not found")
else:
    same = dstr(ei) == dstr(mi) and dstr(el) == dstr(ml)
    today = datetime.date.today().isoformat()
    report("G-004", "Document-control dates preserved from master (not today's date)",
           "PASS" if same else "FAIL",
           f"export initial/last-change: {dstr(ei)} / {dstr(el)}; master: {dstr(mi)} / {dstr(ml)}; today: {today}")

team = find_label_value(wb, r"fmea team|cross-functional team")
report("G-003", "FMEA / CFT team is filled (not blank / 'not provided')",
       "FAIL" if team is None or norm(team) in ("not provided", "") else "PASS", f"value: {txt(team)[:80]}")

my = find_label_value(wb, r"model year")
if my is None:
    report("G-005", "Model Year/Platform has no part-number leakage", "SKIP", "no Model Year field in this export")
else:
    report("G-005", "Model Year/Platform has no part-number leakage",
           "FAIL" if re.search(r"part no|\d{8,}", txt(my), re.I) else "PASS", f"value: {txt(my)[:60]}")

# ---------------------------------------------------------------- row-level checks
report("INFO", "Data rows found in Final PFMEA", "INFO", f"{len(rows)} rows (header row {hdr_row}, sheet '{fp_ws.title}')")

if not skip_missing("G-006", "Work Element is not a sub-operation ID", ["we"]):
    bad = [txt(cell(r, "we"))[:50] for r in rows if re.match(r"^\s*\d+(\.\d+)+", txt(cell(r, "we"))) or txt(cell(r, "we")).startswith("RT-")]
    report("G-006", "Work Element is not a sub-operation ID", "FAIL" if bad else "PASS", f"{len(bad)}/{len(rows)} rows", bad[:3])

if not skip_missing("G-008", "Failure modes are split (no multi-FM rows)", ["fm"]):
    multi = [txt(cell(r, "fm"))[:80] for r in rows if txt(cell(r, "fm")).count(" / ") >= 2 or ";" in txt(cell(r, "fm"))]
    one = [txt(cell(r, "fm"))[:80] for r in rows if txt(cell(r, "fm")).count(" / ") == 1]
    report("G-008", "Failure modes are split (no multi-FM rows)", "REVIEW" if (multi or one) else "PASS",
           f"{len(multi)} rows with 2+ separators (likely several failure modes in one row), {len(one)} with one ' / ' (may be a synonym; judge)",
           multi[:4])

# G-009: tuple must equal one real source tuple
raw = master["01_ALL_SOURCES_GROUPED"] if "01_ALL_SOURCES_GROUPED" in master.sheetnames else None
if raw is None or C["refs"] is None or skip_missing("G-009", "AP tuple is a real source tuple", ["s", "o", "d", "ap"]):
    if raw is not None and C["refs"] is None:
        report("G-009", "S/O/D/AP tuple equals a real source tuple", "SKIP", "no source-row reference column in export")
    elif raw is None:
        report("G-009", "S/O/D/AP tuple equals a real source tuple", "SKIP", "01_ALL_SOURCES_GROUPED not in master")
else:
    tup = {}
    for r in raw.iter_rows(min_row=10, values_only=True):
        if r[0] and r[0] not in tup:
            try: tup[str(r[0])] = (int(r[10]), int(r[13]), int(r[15]), txt(r[16]))
            except Exception: tup[str(r[0])] = None
    bad, checked = [], 0
    for r in rows:
        try: mine = (int(cell(r, "s")), int(cell(r, "o")), int(cell(r, "d")), txt(cell(r, "ap")))
        except Exception: continue
        ids = re.findall(r"[A-Z_]+-\d+|\d{2}\.\d\.\d+", txt(cell(r, "refs")))
        cands = {tup[i] for i in ids if tup.get(i)}
        if not cands: continue
        checked += 1
        if mine not in cands:
            bad.append(f"{txt(cell(r,'sub'))[:28]} export={mine} sources={sorted(cands)[:3]}")
    report("G-009", "S/O/D/AP tuple equals one real source tuple (no synthetic max)", "FAIL" if bad else "PASS",
           f"{len(bad)}/{checked} checkable rows have a tuple no cited source reported", bad[:4])

# AP derivation vs lookup
if "21_AP_LOOKUP" in master.sheetnames and not need(["s", "o", "d", "ap"]):
    lk = {}
    for r in master["21_AP_LOOKUP"].iter_rows(min_row=2, values_only=True):
        if isinstance(r[0], int): lk[(r[0], r[1], r[2])] = txt(r[3])
    bad = []
    for r in rows:
        try:
            k = (int(cell(r, "s")), int(cell(r, "o")), int(cell(r, "d")))
        except Exception: continue
        if lk.get(k) and lk[k] != txt(cell(r, "ap")):
            bad.append(f"{txt(cell(r,'sub'))[:28]} S/O/D={k} export AP={txt(cell(r,'ap'))} lookup={lk[k]}")
    report("AP-LOOKUP", "AP equals the 21_AP_LOOKUP value for its S/O/D", "FAIL" if bad else "PASS", f"{len(bad)} mismatches", bad[:4])

if not skip_missing("G-010", "Incomplete rows are tagged Structural Candidate", ["fe", "s", "o", "d", "ap"]):
    inc = [r for r in rows if any(blank(cell(r, k)) for k in ["fe", "s", "o", "d", "ap"])]
    untagged = [r for r in inc if not any("structural candidate" in norm(x) for x in r if x)]
    report("G-010", "Rows missing FE or S/O/D/AP are tagged 'Structural Candidate / Review Required'",
           "FAIL" if untagged else "PASS", f"{len(inc)} incomplete rows, {len(untagged)} untagged; {len(rows)-len(inc)} complete")

VERB = re.compile(r"\b(add|introduce|implement|install|define|create|provide|improve|consider|should|recommend)\b", re.I)
if not skip_missing("G-011", "Current controls contain no 'Add/Introduce...' actions", ["pc", "dc"]):
    bad = [txt(cell(r, k))[:70].replace("\n", " ") for r in rows for k in ("pc", "dc") if VERB.search(txt(cell(r, k)))]
    report("G-011", "Current controls contain no 'Add/Introduce...' actions", "FAIL" if bad else "PASS", f"{len(bad)} cells", bad[:3])

if not skip_missing("G-012", "Action Taken holds no source-mapping notes", ["taken"]):
    bad = [txt(cell(r, "taken"))[:60] for r in rows if re.search(r"MASTER_REF|BASELINE|Source_ID|Mapping|SITE_D_|SITE_M_|GEMBA|consolidat", txt(cell(r, "taken")), re.I)]
    report("G-012", "Action Taken holds no source-mapping notes", "FAIL" if bad else "PASS", f"{len(bad)}/{len(rows)} rows", bad[:3])

if not skip_missing("G-013", "Owner/date are real or 'Not assigned' (never generic/blank)", ["pa", "da", "owner", "tdate"]):
    act = [r for r in rows if not blank(cell(r, "pa")) or not blank(cell(r, "da"))]
    generic = [r for r in act if re.search(r"core team|tbd|team$", txt(cell(r, "owner")), re.I)]
    nodate = [r for r in act if blank(cell(r, "tdate"))]
    bad = len(generic) + len(nodate)
    report("G-013", "Owner/date are real or 'Not assigned' (never generic/blank)", "FAIL" if bad else "PASS",
           f"{len(act)} rows with actions: {len(generic)} generic-team owners, {len(nodate)} blank dates")

dash = [ws for ws in wb.worksheets if "dashboard" in norm(ws.title)]
dtxt = " ".join(str(c) for ws in dash for row in ws.iter_rows(values_only=True) for c in row if c)
report("G-014", "Dashboard states residual-risk columns are intentionally blank",
       "PASS" if re.search(r"residual", dtxt, re.I) else "FAIL", "no 'residual' wording on the Dashboard sheet" if not re.search(r"residual", dtxt, re.I) else "")

if not skip_missing("G-015", "Source Plant varies per row (not all one value)", ["srcplant"]):
    cnt = Counter(txt(cell(r, "srcplant")) for r in rows)
    report("G-015", "Source Plant varies per row (not all one value)", "FAIL" if len(cnt) == 1 and len(rows) > 5 else "PASS", f"distribution: {dict(cnt)}")

if not skip_missing("G-016", "Source File is labeled as derived/consolidated", ["srcfile"]):
    cnt = Counter(txt(cell(r, "srcfile"))[:60] for r in rows)
    lab = all(re.search(r"derived|consolidat", k, re.I) for k in cnt)
    report("G-016", "Source File is labeled as derived/consolidated", "PASS" if lab else "REVIEW", f"distinct values: {dict(cnt)}")

if not skip_missing("G-017", "Original rating method is not the normalized AP method", ["origmeth"]):
    bad = [txt(cell(r, "origmeth"))[:60] for r in rows if re.search(r"AIAG/VDA AP rule mapped|mapped to H/M/L", txt(cell(r, "origmeth")), re.I)]
    report("G-017", "Original rating method is not the normalized AP method", "FAIL" if bad else "PASS", f"{len(bad)}/{len(rows)} rows", bad[:2])

if not skip_missing("G-021", "Duplicate-elimination key is a content signature, not a route ID", ["dupkey"]):
    keys = [txt(cell(r, "dupkey")) for r in rows]
    routeish = sum(1 for k in keys if k.startswith("RT-"))
    report("G-021", "Duplicate-elimination key is a content signature, not a route ID",
           "FAIL" if routeish > len(keys) * 0.5 else "PASS", f"{routeish}/{len(keys)} keys look like route IDs", keys[:2])

main_keys = ["fm", "fe", "cause", "pc", "dc", "pa", "da"]
HARD = re.compile(r"[\u0600-\u06FF\u4e00-\u9fff]|[éèàçùêâîôû]|Inetrn|Ckeck")
SOFT = re.compile(r"^\s*Interne\s*:|\b(le|la|les|des|pour|avec|dans|une|du)\b", re.I)
hard_rows, soft_rows = [], []
for r in rows:
    for k in main_keys:
        if C.get(k) is None: continue
        t = txt(cell(r, k))
        if HARD.search(t):
            hard_rows.append(f"{txt(cell(r,'sub'))[:24]} [{k}] {t[:50]}"); break
        if SOFT.search(t):
            soft_rows.append(f"{txt(cell(r,'sub'))[:24]} [{k}] {t[:50]}"); break
report("G-022", "No non-English script/accents/known typos in main content fields",
       "FAIL" if hard_rows else "PASS", f"{len(hard_rows)}/{len(rows)} rows", hard_rows[:3])
report("G-022b", "No French-style prefixes ('Interne :') or French words in main fields",
       "REVIEW" if soft_rows else "PASS", f"{len(soft_rows)}/{len(rows)} rows (heuristic)", soft_rows[:3])

pipe = [f"{txt(cell(r,'sub'))[:24]} [{k}]" for r in rows for k in ("fe", "pc", "dc", "cause") if C.get(k) is not None and " | " in txt(cell(r, k))]
report("G-023", "No raw ' | ' concatenation in effect/cause/control text", "FAIL" if pipe else "PASS", f"{len(pipe)} cells", pipe[:3])

art = []
for r in rows:
    for i in range(2, 32):
        v = r[i]
        if v is not None and re.fullmatch(r"\s*184\s*", str(v)) or (isinstance(v, str) and re.search(r"(?<![\d.])184(?![\d.])", v)):
            art.append(f"{txt(cell(r,'sub'))[:24]} col {i}")
sc_bad = []
if C["sc"] is not None:
    sc_bad = [txt(cell(r, "sc")) for r in rows if re.fullmatch(r"\d+|L|M|H", txt(cell(r, "sc")))]
report("G-024", "No stray '184' / numeric or L-M-H values in content or Special Characteristics",
       "FAIL" if art or sc_bad else "PASS", f"{len(art)} '184' hits, {len(sc_bad)} bad Special Characteristics values", (art + sc_bad)[:3])

if not skip_missing("G-030", "PFC link points to the row's own OP node", ["pfc", "sub"]):
    bad = []
    for r in rows:
        op = re.search(r"OP\d+A?", txt(cell(r, "sub")))
        if not op: continue
        opc, link = op.group(0), txt(cell(r, "pfc"))
        link_ops = re.findall(r"OP\d+A?", link)
        if opc.endswith("A") and not re.search(r"13_PFC_DIAGRAM|14_PFC_CARTOGRAPHY", link):
            bad.append(f"{opc}: link='{link[:45]}' (route-specific rows must use 13_PFC_DIAGRAM / 14_PFC_CARTOGRAPHY)")
        elif link_ops and opc not in link_ops:
            bad.append(f"{opc}: link='{link[:45]}' points to a different OP")
    report("G-030", "PFC link points to the row's own OP node", "FAIL" if bad else "PASS", f"{len(bad)} rows", bad[:4])

# G-018 / G-020: cited IDs exist in master raw sheet; duplicates in master raw
if raw is not None and C["refs"] is not None:
    ids_master = Counter(str(r[0]) for r in raw.iter_rows(min_row=10, values_only=True) if r[0])
    cited = set()
    for r in rows: cited |= set(re.findall(r"[A-Z_]+-\d+|\d{2}\.\d\.\d+", txt(cell(r, "refs"))))
    missing = sorted(i for i in cited if i not in ids_master)
    dup = sorted(i for i in cited if ids_master.get(i, 0) > 1)
    report("G-018", "Every cited source/evidence ID exists in the master raw sheet", "FAIL" if missing else "PASS", f"{len(cited)} IDs cited, {len(missing)} not found", missing[:4])
    report("G-020", "Cited IDs are not ambiguous duplicates in the master", "REVIEW" if dup else "PASS", f"{len(dup)} cited IDs occur more than once in the master", dup[:4])
else:
    report("G-018", "Cited IDs exist in master", "SKIP", "source-reference column or raw sheet missing")

# manual-only reminders
for gid, nm, why in [
    ("G-002", "Customer/plant prefilled, not asked blind", "chat behavior - check the conversation"),
    ("G-007", "Function hierarchy is 3 distinct levels", "judgment - compare columns F/G/H in 3 rows"),
    ("G-019", "Evidence limited to this export's rows", "no evidence sheet in the 6-sheet structure; nothing to check"),
]:
    report(gid, nm, "SKIP", why)

# ---------------------------------------------------------------- print
order = {"FAIL": 0, "REVIEW": 1, "SKIP": 2, "INFO": 3, "PASS": 4}
print(f"\nExport : {exp_path}\nMaster : {master_path}\n")
print(f"{'ID':<10}{'STATUS':<8}CHECK")
print("-" * 100)
for gid, name, st, detail, ex in sorted(results, key=lambda x: (order[x[2]], x[0])):
    print(f"{gid:<10}{st:<8}{name}")
    if detail: print(f"{'':<18}{detail}")
    for e in ex: print(f"{'':<18}e.g. {e}")
c = Counter(r[2] for r in results)
print("-" * 100)
print("TOTAL:", dict(c))
