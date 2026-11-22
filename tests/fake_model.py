"""A fake 'model' for offline tests: builds the JSON the LLM would return by
looking up real span IDs for each phrase. No network calls.

Each phrase is located as a run of consecutive spans whose concatenated text
matches. Use "@N" suffix to pick the N-th occurrence (default first; occurrences
are counted in reading order).
"""
from __future__ import annotations

from app.checks import normalize


def find_ids(spans: list[dict], phrase: str) -> list[str]:
    nth = 0
    if "@" in phrase:
        phrase, n = phrase.rsplit("@", 1)
        nth = int(n)
    target = normalize(phrase)
    hits = []
    for i in range(len(spans)):
        acc = ""
        for j in range(i, min(i + 12, len(spans))):
            acc += normalize(spans[j]["text"])
            if acc == target:
                hits.append([s["id"] for s in spans[i:j + 1]])
                break
            if not target.startswith(acc):
                break
    if len(hits) <= nth:
        raise LookupError(f"phrase not found: {phrase!r}")
    return hits[nth]


def F(spans, value, phrase=None, conf="high", reason=""):
    if value is None:
        return {"value": None, "source_span_ids": [], "confidence": conf if conf != "high" else "low", "reason": reason or "Not on drawing"}
    return {"value": value, "source_span_ids": find_ids(spans, phrase or value), "confidence": conf, "reason": reason}


def _title(spans, d):
    return {k: F(spans, *v) if isinstance(v, tuple) else F(spans, v) for k, v in d.items()}


def response_for(sample: str, spans: list[dict]) -> dict:
    S = spans
    if sample.startswith("01"):
        return {
            "title_block": _title(S, {
                "part_number": "BRK-1001", "part_name": "MOUNTING BRACKET", "revision": ("C", "C@2"),
                "material": "6061-T6 ALUMINUM", "finish": ("CLEAR ANODIZE", "CLEAR ANODIZE@1"),
                "general_tolerance": "X.X ±0.2 X.XX ±0.05 ANGLES ±0.5°", "units": ("mm", "MM"),
                "scale": "1:1", "drawn_by": "J. MARTINEZ", "date": ("2026-03-14", "2026-03-14@1"),
            }),
            "bom_lines": [],
            "dimensions": [
                {"feature": "Overall width", "nominal": F(S, "120.00"), "tolerance": F(S, "±0.10", "±0.10@2"), "unit": "mm"},
                {"feature": "Overall height", "nominal": F(S, "80.00"), "tolerance": F(S, "±0.10", "±0.10@0"), "unit": "mm"},
                {"feature": "Hole spacing", "nominal": F(S, "90.00"), "tolerance": F(S, "±0.05"), "unit": "mm"},
                {"feature": "Hole edge distance", "nominal": F(S, "15.00"), "tolerance": F(S, None, conf="medium", reason="Uses general tolerance"), "unit": "mm"},
                {"feature": "Hole height from bottom", "nominal": F(S, "60.00"), "tolerance": F(S, "±0.10", "±0.10@1"), "unit": "mm"},
                {"feature": "Mounting holes", "nominal": F(S, "2X Ø8.50"), "tolerance": F(S, None, conf="medium", reason="Uses general tolerance"), "unit": "mm"},
                {"feature": "Centre bore", "nominal": F(S, "Ø25.00"), "tolerance": F(S, "H7"), "unit": "mm"},
                {"feature": "Thickness", "nominal": F(S, "6.00"), "tolerance": F(S, "±0.05", "±0.05@1"), "unit": "mm"},
                {"feature": "Flange length", "nominal": F(S, "40.00"), "tolerance": F(S, "±0.20"), "unit": "mm"},
                {"feature": "Bend angle", "nominal": F(S, "90°"), "tolerance": F(S, "±0.5°"), "unit": "deg"},
            ],
            "notes": [
                {"category": "finish", "text": F(S, "FINISH: CLEAR ANODIZE PER MIL-A-8625 TYPE II, CLASS 1.")},
                {"category": "surface_finish", "text": F(S, "SURFACE FINISH Ra 3.2 µm UNLESS OTHERWISE SPECIFIED.")},
                {"category": "hole_callout", "text": F(S, "2X Ø8.50 THRU")},
                {"category": "general", "text": F(S, "BREAK ALL SHARP EDGES 0.2 MAX.")},
            ],
        }
    if sample.startswith("02"):
        bom = []
        for item, pn, desc, mat, qty in [("1", "BRK-2041", "BASE PLATE", "6061-T6", "1"),
                                         ("2", "BRK-2042", "SIDE SUPPORT", "6061-T6", "2"),
                                         ("3", "SHF-2043", "ROLLER SHAFT Ø12 h6", "AISI 304", "1"),
                                         ("4", "ROL-2044", "ROLLER Ø40 x 30", "POM (ACETAL)", "1"),
                                         ("5", "SCR-M6-16", "SOCKET HEAD CAP SCREW M6x16", "STEEL CL 12.9", "4")]:
            bom.append(_bom_line(S, item, pn, desc, mat, qty))
        return {
            "title_block": _title(S, {
                "part_number": "ASM-2040", "part_name": "ROLLER GUIDE ASSEMBLY", "revision": ("A", "A@1"),
                "material": ("SEE PARTS LIST", "SEE PARTS LIST"), "finish": "BLACK ANODIZE (ITEMS 1, 2)",
                "general_tolerance": "X.X ±0.5 X.XX ±0.10 ANGLES ±1°", "units": ("mm", "MM"),
                "scale": "5:4", "drawn_by": "P. KOWALSKI", "date": ("2026-05-20", "2026-05-20@1"),
            }),
            "bom_lines": bom,
            "dimensions": [
                {"feature": "Overall length", "nominal": F(S, "160.0"), "tolerance": F(S, "±0.5", "±0.5@1"), "unit": "mm"},
                {"feature": "Overall height", "nominal": F(S, "95.0"), "tolerance": F(S, "±0.5", "±0.5@0"), "unit": "mm"},
                {"feature": "Shaft centre height", "nominal": F(S, "62.00"), "tolerance": F(S, "±0.10", "±0.10@0"), "unit": "mm"},
                {"feature": "Inside width between supports", "nominal": F(S, "100.00"), "tolerance": F(S, "+0.20/-0.00"), "unit": "mm"},
                {"feature": "Roller diameter", "nominal": F(S, "Ø40.00"), "tolerance": F(S, "±0.05"), "unit": "mm"},
                {"feature": "Shaft diameter", "nominal": F(S, "Ø12", "Ø12@0"), "tolerance": F(S, "h6", "h6@0"), "unit": "mm"},
            ],
            "notes": [
                {"category": "thread", "text": F(S, "TORQUE ITEM 5 SCREWS TO 10 N·m.")},
                {"category": "thread", "text": F(S, "APPLY LOCTITE 243 TO ALL SCREW THREADS.")},
                {"category": "finish", "text": F(S, "ITEMS 1 AND 2: BLACK ANODIZE PER MIL-A-8625 TYPE II.")},
            ],
        }
    # 03: the problem drawing
    return {
        "title_block": {
            **_title(S, {
                "part_number": "SMP-3307", "part_name": ("SENSOR MOUNT PLATE", "SENSOR MOUNT PLATE@1"),
                "material": ("ALUMINUM OR STEEL (SEE NOTE 4)", "ALUMINUM OR STEEL (SEE NOTE 4)"),
                "finish": "NONE", "general_tolerance": "X.X ±0.2 X.XX ±0.05", "units": ("mm", "MM"),
                "scale": ("2:1", "2:1"), "drawn_by": "T. NAKAMURA", "date": "2026-08-09",
            }),
            "revision": F(S, None, conf="low", reason="REV field in the title block is blank"),
        } | {"material": {**F(S, "ALUMINUM OR STEEL (SEE NOTE 4)"), "confidence": "low",
                          "reason": "Two alternative materials given; note 4 says material is TBD"}},
        "bom_lines": [
            _bom_line(S, "1", "SMP-3307", "SENSOR MOUNT PLATE", "SEE NOTE 4", "1"),
            _bom_line(S, "2", "STO-M4-10", "HEX STANDOFF M4 x 10", "BRASS", "0"),
            _bom_line(S, "3", "SCR-M4-08", "PAN HEAD SCREW M4x8", "A2 STAINLESS", "4"),
            _bom_line(S, "4", "WSH-M4", "FLAT WASHER M4", "A2 STAINLESS", "4"),
        ],
        "dimensions": [
            {"feature": "Plate length", "nominal": F(S, "90.00"), "tolerance": F(S, "±0.10", "±0.10@1"), "unit": "mm"},
            {"feature": "Plate width", "nominal": F(S, "60.00"), "tolerance": F(S, "±0.10", "±0.10@0"), "unit": "mm"},
            {"feature": "Hole pattern X", "nominal": F(S, "76.00"), "tolerance": F(S, "±0.05", "±0.05@0"), "unit": "mm"},
            {"feature": "Hole pattern Y", "nominal": F(S, "46.00"), "tolerance": F(S, "±0.05", "±0.05@2"), "unit": "mm"},
            {"feature": "Slot length", "nominal": F(S, '2.50"'), "tolerance": F(S, None, conf="medium", reason="Uses general tolerance"), "unit": "inch"},
            {"feature": "Slot width", "nominal": F(S, "8.00"), "tolerance": F(S, "+0.1"), "unit": "mm"},
            {"feature": "Thickness", "nominal": F(S, "5.00"), "tolerance": F(S, "±0.05", "±0.05@1"), "unit": "mm"},
            {"feature": "Mounting holes", "nominal": F(S, "4X Ø4.50"), "tolerance": F(S, None, conf="medium", reason="Uses general tolerance"), "unit": "mm"},
        ],
        "notes": [
            {"category": "surface_finish", "text": F(S, "SURFACE FINISH Ra 1.6 µm ON SENSOR FACE.")},
            {"category": "general", "text": {**F(S, "MATERIAL TBD - ALUMINUM OR STEEL, CONFIRM WITH CUSTOMER BEFORE ORDER."),
                                             "confidence": "medium", "reason": "Material not finalised"}},
        ],
    }


def _bom_line(S, item, pn, desc, mat, qty):
    """Locate a BOM row by its part-number span, then take item/qty from the same table row."""
    pn_ids = find_ids(S, pn)
    by_id = {s["id"]: s for s in S}
    row_y, pn_x = by_id[pn_ids[0]]["bbox"][1], by_id[pn_ids[0]]["bbox"][0]
    same_row = [s for s in S if abs(s["bbox"][1] - row_y) < 3 and pn_x - 60 < s["bbox"][0] < pn_x + 400]
    left = min(same_row, key=lambda s: s["bbox"][0])
    right = max(same_row, key=lambda s: s["bbox"][0])
    desc_ids = _ids_in_row(same_row, desc)
    mat_ids = _ids_in_row(same_row, mat)
    return {
        "item_no": {"value": item, "source_span_ids": [left["id"]], "confidence": "high", "reason": ""},
        "part_number": {"value": pn, "source_span_ids": pn_ids, "confidence": "high", "reason": ""},
        "description": {"value": desc, "source_span_ids": desc_ids, "confidence": "high", "reason": ""},
        "material": {"value": mat, "source_span_ids": mat_ids, "confidence": "high" if "NOTE" not in mat else "low",
                     "reason": "" if "NOTE" not in mat else "Material deferred to note 4, which is TBD"},
        "quantity": {"value": qty, "source_span_ids": [right["id"]], "confidence": "high", "reason": ""},
    }


def _ids_in_row(row_spans, phrase):
    row_spans = sorted(row_spans, key=lambda s: s["bbox"][0])
    return find_ids(row_spans, phrase)
