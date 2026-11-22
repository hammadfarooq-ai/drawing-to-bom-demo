"""Source verification and rule-based integrity checks.

Data model used here (built in pipeline.py):
  row   = {"row_id", "section", "label", "fields": {name: field}, ...}
  field = {"value", "source_span_ids", "confidence", "reason",
           "verified", "verify_msg", "boxes", "human", "status", "issues"}
A check target is "<row_id>:<field_name>", e.g. "bom.2:quantity".
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- normalization

_CHAR_MAP = {
    "∅": "ø", "⌀": "ø", "Ø": "ø",
    "–": "-", "—": "-", "−": "-", "‐": "-",
    "“": '"', "”": '"', "″": '"', "’": "'", "‘": "'", "′": "'",
    "×": "x", "µ": "u", "μ": "u",
}


def normalize(text: str | None) -> str:
    """Lowercase, unify look-alike symbols and drop all whitespace."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    for a, b in _CHAR_MAP.items():
        s = s.replace(a, b)
    s = s.lower().replace("+/-", "±").replace("+-", "±")
    return re.sub(r"\s+", "", s)


_UNIT_ALIASES = {
    "mm": ["mm", "millimet", "metric"],
    "inch": ["inch", "inches", "in.", '"'],
}


def value_in_spans(value: str | None, span_texts: list[str], key: str | None = None) -> bool:
    """True if the normalized value appears in the normalized concatenation of the spans."""
    nv = normalize(value).strip(".,;:")
    if not nv:
        return False
    joined = normalize("".join(span_texts))
    if nv in joined:
        return True
    if key == "units":
        for alias in _UNIT_ALIASES.get(nv, []):
            if alias in joined:
                return True
    return False


def union_boxes(spans: list[dict]) -> list[dict]:
    """One box per page covering all cited spans on that page."""
    by_page: dict[int, list[float]] = {}
    for s in spans:
        x0, y0, x1, y1 = s["bbox"]
        b = by_page.get(s["page"])
        by_page[s["page"]] = [x0, y0, x1, y1] if b is None else [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
    return [{"page": p, "bbox": [round(v, 2) for v in b]} for p, b in sorted(by_page.items())]


def verify_field(field: dict, span_index: dict[str, dict], key: str | None = None) -> dict:
    """Resolve cited span IDs to boxes and check the value really appears there."""
    value = field.get("value")
    ids = [i for i in (field.get("source_span_ids") or []) if isinstance(i, str)]
    field["source_span_ids"] = ids
    field["boxes"] = []
    if value is None or str(value).strip() == "":
        field["value"] = None
        field["verified"] = None
        field["verify_msg"] = "No value extracted"
        return field
    missing = [i for i in ids if i not in span_index]
    found = [span_index[i] for i in ids if i in span_index]
    # sort cited spans in reading order (IDs are assigned in reading order)
    found.sort(key=lambda s: (s["page"], s["id"]))
    field["boxes"] = union_boxes(found)
    if not ids:
        field["verified"], field["verify_msg"] = False, "Model cited no source text"
    elif missing:
        field["verified"], field["verify_msg"] = False, f"Cited span(s) don't exist: {', '.join(missing[:3])}"
    elif not value_in_spans(value, [s["text"] for s in found], key):
        cited = " ".join(s["text"] for s in found)[:80]
        field["verified"], field["verify_msg"] = False, f"Value not found in cited text \"{cited}\""
    else:
        field["verified"], field["verify_msg"] = True, "Value matches cited text"
    return field


# ---------------------------------------------------------------- value parsers

_NUM_RE = re.compile(r"\d+(?:\.\d+)?(?:/\d+)?|\.\d+")


def parse_nominal(text: str | None) -> float | None:
    """Pull the main number out of a dimension like '2X Ø8.50', 'R6', 'M6x1.0', '45°', '1/4'."""
    if not text:
        return None
    s = normalize(text)
    s = re.sub(r"^\d+x(?=[ørms\d.])", "", s)  # leading "2X " multiplier
    # only dimension symbols may precede the number
    m = re.match(r"^[ørms(\[~≈]*(" + _NUM_RE.pattern + ")", s)
    if not m:
        return None
    tok = m.group(1)
    if "/" in tok:
        a, b = tok.split("/")
        return float(a) / float(b) if float(b) else None
    return float(tok)


_N = r"\d*\.?\d+"
_TOL_PATTERNS = [
    rf"^±{_N}°?$",                               # ±0.05
    rf"^\+{_N}/?-{_N}$",                          # +0.10/-0.05 or +0.10-0.05
    rf"^-{_N}/?\+{_N}$",                          # -0.05/+0.10
    rf"^\+{_N}/?[-±]?0$",                         # +0.1/0
    rf"^{_N}/{_N}$",                              # limits 25.05/24.95
    rf"^{_N}-{_N}$",                              # limits 24.95-25.05
    r"^[a-z]{1,2}\d{1,2}(/[a-z]{1,2}\d{1,2})?$",  # fit H7, g6, H7/g6
    rf"^{_N}max$", rf"^{_N}min$", rf"^max{_N}$", rf"^min{_N}$",
]


def tolerance_valid(text: str | None) -> bool:
    if text is None:
        return True  # falls back to general tolerance
    s = normalize(text).replace("mm", "").replace('"', "")
    s = s.replace("°", "") if s.count("°") else s
    return any(re.match(p, s) for p in _TOL_PATTERNS)


def parse_quantity(text: str | None) -> int | None:
    if text is None:
        return None
    s = normalize(text).rstrip("x").replace("pcs", "").replace("pc", "").replace("ea", "")
    if re.fullmatch(r"\d+", s):
        return int(s)
    return None


MATERIAL_PATTERNS = [
    r"\b(aluminum|aluminium|al)\b", r"\b[1-7]\d{3}(-t\d+)?\b", r"\baisi\b", r"\bsae\b", r"\bastm\b",
    r"\bstainless\b", r"\bss\s?3\d\d\b", r"\b30[34]l?\b", r"\b316l?\b", r"\b17-4\b", r"\ba[24]\b",
    r"\bsteel\b", r"\b10[1-9]\d\b", r"\b4140\b", r"\b4340\b", r"\ba36\b", r"\bcrs\b", r"\bhrs\b",
    r"\bbrass\b", r"\bc36000\b", r"\bc360\b", r"\bcopper\b", r"\bbronze\b", r"\bcast iron\b",
    r"\btitanium\b", r"\bti-6al-4v\b", r"\bzinc\b", r"\bmagnesium\b",
    r"\babs\b", r"\bnylon\b", r"\bpa\s?6+\b", r"\bpom\b", r"\bdelrin\b", r"\bacetal\b", r"\bpeek\b",
    r"\bptfe\b", r"\bpolycarbonate\b", r"\bpc\b", r"\bpp\b", r"\bhdpe\b", r"\buhmw\b", r"\bacrylic\b",
    r"\bpmma\b", r"\brubber\b", r"\bepdm\b", r"\bnbr\b", r"\bsilicone\b", r"\bfr-?4\b",
]
AMBIGUOUS_PATTERNS = [r"\bor\b", r"\btbd\b", r"\btbc\b", r"\bsee note\b", r"\bequiv", r"\?", r"\bto be confirmed\b", r"\bcustomer\b"]
SEE_BOM_PATTERNS = [r"see (parts list|bom|p/l)", r"as (noted|listed)", r"per (parts list|bom)"]
NOT_APPLICABLE = {"-", "--", "n/a", "na", "—", "purchased", "std", "commercial"}


def classify_material(text: str | None) -> tuple[str, str]:
    """Return (status, message) for a material string."""
    if not text:
        return "fail", "No material specified"
    low = " " + str(text).lower() + " "
    if any(re.search(p, low) for p in SEE_BOM_PATTERNS):
        return "refer_bom", "Material is specified per parts list"
    if any(re.search(p, low) for p in AMBIGUOUS_PATTERNS):
        return "warn", f"Material \"{text}\" is ambiguous; confirm a single spec"
    if any(re.search(p, low) for p in MATERIAL_PATTERNS):
        return "pass", f"\"{text}\" looks like a recognised material"
    return "warn", f"\"{text}\" is not a recognised material spec"


# ---------------------------------------------------------------- checks

REQUIRED_TITLE_FIELDS = ["part_number", "material", "revision", "units"]


def _check(cid: str, name: str, status: str, message: str, targets: list[str] | None = None) -> dict:
    return {"id": cid, "name": name, "status": status, "message": message, "targets": targets or []}


def _rows(rows: list[dict], section: str) -> list[dict]:
    return [r for r in rows if r["section"] == section]


def _drawing_units(rows: list[dict]) -> str | None:
    for r in _rows(rows, "title"):
        if r["key"] == "units":
            v = normalize(r["fields"]["value"].get("value"))
            if v.startswith("mm") or "millimet" in v or "metric" in v:
                return "mm"
            if v.startswith("in") or v == '"':
                return "inch"
    return None


def run_checks(rows: list[dict]) -> list[dict]:
    checks: list[dict] = []

    # 1. Source check
    unverified = []
    for r in rows:
        for name, f in r["fields"].items():
            if f.get("value") is not None and not f.get("human") and f.get("verified") is False:
                unverified.append((r, name, f))
    if unverified:
        detail = "; ".join(f"{r['label']}{'' if r['section'] == 'title' else ' ' + name}: {f['verify_msg']}" for r, name, f in unverified[:4])
        checks.append(_check("source", "Source check", "warn",
                             f"{len(unverified)} value(s) could not be matched to their cited text. {detail}",
                             [f"{r['row_id']}:{n}" for r, n, _ in unverified]))
    else:
        checks.append(_check("source", "Source check", "pass", "Every extracted value was found in the drawing text it cites."))

    # 2. Required fields
    title = {r["key"]: r for r in _rows(rows, "title")}
    for key in REQUIRED_TITLE_FIELDS:
        r = title.get(key)
        val = r["fields"]["value"].get("value") if r else None
        label = r["label"] if r else key
        if val is None or str(val).strip() == "":
            checks.append(_check(f"required.{key}", f"Required: {label}", "fail",
                                 f"{label} is missing from the title block.", [f"tb.{key}:value"]))
        else:
            checks.append(_check(f"required.{key}", f"Required: {label}", "pass", f"{label} present: {val}"))

    # 3. Material
    bom = _rows(rows, "bom")
    if "material" in title:
        mval = title["material"]["fields"]["value"].get("value")
        if mval is not None:
            st, msg = classify_material(mval)
            if st == "refer_bom":
                bom_mats = [b["fields"]["material"].get("value") for b in bom]
                st, msg = ("pass", "Assembly: materials are given per BOM line") if bom and all(bom_mats) else ("warn", "Title block says see parts list, but BOM materials are incomplete")
            checks.append(_check("material.title", "Material spec (title block)", st, msg, [] if st == "pass" else ["tb.material:value"]))
    for b in bom:
        mval = b["fields"]["material"].get("value")
        if mval is None or normalize(mval) in NOT_APPLICABLE:
            continue
        st, msg = classify_material(mval)
        if st not in ("pass", "refer_bom"):
            checks.append(_check(f"material.{b['row_id']}", f"Material spec ({b['label']})", "warn", msg, [f"{b['row_id']}:material"]))

    # 4. Quantity positive integer
    if bom:
        bad = []
        for b in bom:
            q = b["fields"]["quantity"].get("value")
            n = parse_quantity(q)
            if n is None or n <= 0:
                bad.append(b)
                reason = "missing" if q is None else f"\"{q}\" is not a positive integer"
                checks.append(_check(f"qty.{b['row_id']}", f"Quantity ({b['label']})", "fail",
                                     f"Quantity {reason}.", [f"{b['row_id']}:quantity"]))
        if not bad:
            checks.append(_check("qty", "BOM quantities", "pass", f"All {len(bom)} quantities are positive integers."))

        # 5. Duplicate item numbers
        seen: dict[str, list[dict]] = {}
        for b in bom:
            item = normalize(b["fields"]["item_no"].get("value"))
            if item:
                seen.setdefault(item, []).append(b)
        dups = {k: v for k, v in seen.items() if len(v) > 1}
        if dups:
            for k, v in dups.items():
                checks.append(_check(f"dup.{k}", f"Duplicate item no. {k}", "fail",
                                     f"Item number {k} appears {len(v)} times in the BOM.",
                                     [f"{b['row_id']}:item_no" for b in v]))
        else:
            checks.append(_check("dup", "BOM item numbers", "pass", "No duplicate item numbers."))

    # 6. Dimensions: numeric, tolerance format, unit consistency
    units = _drawing_units(rows)
    dims = _rows(rows, "dim")
    dim_problems = 0
    for d in dims:
        nom = d["fields"]["nominal"].get("value")
        tol = d["fields"]["tolerance"].get("value")
        if parse_nominal(nom) is None:
            dim_problems += 1
            checks.append(_check(f"dimnum.{d['row_id']}", f"Dimension value ({d['label']})", "fail",
                                 f"Nominal \"{nom}\" does not parse as a number.", [f"{d['row_id']}:nominal"]))
        if not tolerance_valid(tol):
            dim_problems += 1
            checks.append(_check(f"tol.{d['row_id']}", f"Tolerance format ({d['label']})", "warn",
                                 f"Tolerance \"{tol}\" is not a recognised format (±x, +x/-y, limits, or fit).", [f"{d['row_id']}:tolerance"]))
        unit = d.get("unit")
        looks_inch = bool(nom) and ('"' in str(nom) or re.search(r"\d\s*in\b", str(nom).lower()))
        looks_mm = bool(nom) and re.search(r"\d\s*mm\b", str(nom).lower())
        found_unit = None
        if units and unit in ("mm", "inch") and unit != units:
            found_unit = unit
        elif units == "mm" and looks_inch:
            found_unit = "inch"
        elif units == "inch" and looks_mm:
            found_unit = "mm"
        if found_unit:
            dim_problems += 1
            checks.append(_check(f"unit.{d['row_id']}", f"Units ({d['label']})", "warn",
                                 f"Dimension \"{nom}\" appears to be in {found_unit} but the drawing is in {units}.",
                                 [f"{d['row_id']}:nominal"]))
    if dims and dim_problems == 0:
        checks.append(_check("dims", "Dimensions", "pass", f"All {len(dims)} dimensions parse, tolerances are well-formed and units are consistent{f' ({units})' if units else ''}."))
    elif not dims:
        checks.append(_check("dims", "Dimensions", "warn", "No dimensions were extracted."))
    return checks


# ---------------------------------------------------------------- statuses

_RANK = {"verified": 0, "needs_review": 1, "failed": 2}


def apply_statuses(rows: list[dict], checks: list[dict]) -> None:
    """Set field.status/issues and row.status from confidence, verification and checks."""
    by_target: dict[str, list[dict]] = {}
    for c in checks:
        for t in c["targets"]:
            by_target.setdefault(t, []).append(c)
    for r in rows:
        worst = "verified"
        for name, f in r["fields"].items():
            issues: list[str] = []
            status = "verified"
            # A dimension without its own tolerance is normal (general tolerance applies).
            expected_null = r["section"] == "dim" and name == "tolerance" and f.get("value") is None
            if expected_null:
                issues.append("No individual tolerance; general tolerance applies")
            elif not f.get("human"):
                if f.get("confidence") == "low":
                    status = "needs_review"
                    issues.append(f"Low confidence: {f.get('reason') or 'model was unsure'}")
                elif f.get("confidence") == "medium" and f.get("reason"):
                    issues.append(f"Medium confidence: {f['reason']}")
                if f.get("verified") is False:
                    status = "needs_review"
                    issues.append(f"Unverified: {f.get('verify_msg')}")
            for c in by_target.get(f"{r['row_id']}:{name}", []):
                if c["id"] == "source":
                    continue  # already covered by verification above
                issues.append(f"{c['name']}: {c['message']}")
                if c["status"] == "fail":
                    status = "failed"
                elif c["status"] == "warn" and status != "failed":
                    status = "needs_review"
            f["status"], f["issues"] = status, issues
            if _RANK[status] > _RANK[worst]:
                worst = status
        r["status"] = worst
