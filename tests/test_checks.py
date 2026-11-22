"""Unit tests for source matching and integrity checks. No API calls."""
import pytest

from app import checks as ck


def span(i, text, x=0, y=0):
    return {"id": f"p1_s{i:03d}", "page": 1, "text": text, "bbox": [x, y, x + 10, y + 5]}


SPANS = [span(1, "6061-T6", 0, 0), span(2, "ALUMINUM", 12, 0), span(3, "±0.10", 30, 0),
         span(4, "Ø8.50", 50, 0), span(5, "UNITS:", 60, 0), span(6, "MILLIMETERS", 72, 0), span(7, "+/-0.05", 90, 0)]
INDEX = {s["id"]: s for s in SPANS}


def field(value, ids, conf="high", reason=""):
    return {"value": value, "source_span_ids": ids, "confidence": conf, "reason": reason, "human": False}


# ------------------------------------------------------------ source matching

class TestSourceMatching:
    def test_multi_span_value_matches(self):
        f = ck.verify_field(field("6061-T6 Aluminum", ["p1_s001", "p1_s002"]), INDEX)
        assert f["verified"] is True
        assert f["boxes"] == [{"page": 1, "bbox": [0, 0, 22, 5]}]

    def test_symbol_variants_normalize(self):
        assert ck.value_in_spans("∅8.50", ["Ø8.50"])
        assert ck.value_in_spans("±0.05", ["+/-0.05"])
        assert ck.value_in_spans("120.00 ± 0.10", ["120.00", "±0.10"])

    def test_cited_id_that_does_not_exist(self):
        f = ck.verify_field(field("6061-T6", ["p1_s999"]), INDEX)
        assert f["verified"] is False
        assert "don't exist" in f["verify_msg"]

    def test_value_not_in_cited_spans(self):
        f = ck.verify_field(field("7075-T6", ["p1_s001"]), INDEX)
        assert f["verified"] is False
        assert "not found" in f["verify_msg"]

    def test_no_citation(self):
        f = ck.verify_field(field("6061-T6", []), INDEX)
        assert f["verified"] is False

    def test_null_value_is_not_verified_or_failed(self):
        f = ck.verify_field(field(None, [], "low"), INDEX)
        assert f["verified"] is None and f["boxes"] == []

    def test_units_alias(self):
        f = ck.verify_field(field("mm", ["p1_s005", "p1_s006"]), INDEX, key="units")
        assert f["verified"] is True
        f = ck.verify_field(field("inch", ["p1_s006"]), INDEX, key="units")
        assert f["verified"] is False

    def test_partial_overlap_is_not_enough(self):
        # "8.5" is a substring of "Ø8.50" - allowed; but a different number is not
        assert ck.value_in_spans("8.50", ["Ø8.50"])
        assert not ck.value_in_spans("8.55", ["Ø8.50"])


# ------------------------------------------------------------ parsers

@pytest.mark.parametrize("text,expected", [
    ("120.00", 120.0), ("2X Ø8.50", 8.5), ("R6", 6.0), ("M6x1.0", 6.0), ("90°", 90.0),
    ("1/4", 0.25), ('2.50"', 2.5), ("Ø25.00", 25.0), ("SEE NOTE", None), ("", None), (None, None),
])
def test_parse_nominal(text, expected):
    assert ck.parse_nominal(text) == expected


@pytest.mark.parametrize("text,ok", [
    ("±0.05", True), ("+/-0.1", True), ("+0.20/-0.00", True), ("+0.10 -0.05", True), ("±0.5°", True),
    ("H7", True), ("h6", True), ("H7/g6", True), ("25.05/24.95", True), (None, True),
    ("+0.1", False), ("approx", False), ("0.05±", False),
])
def test_tolerance_valid(text, ok):
    assert ck.tolerance_valid(text) is ok


@pytest.mark.parametrize("text,expected", [("4", 4), ("0", 0), ("2X", 2), ("10 PCS", 10), ("1.5", None), ("A/R", None), (None, None)])
def test_parse_quantity(text, expected):
    assert ck.parse_quantity(text) == expected


@pytest.mark.parametrize("text,status", [
    ("6061-T6 ALUMINUM", "pass"), ("AISI 304", "pass"), ("Steel", "pass"), ("ABS", "pass"),
    ("POM (ACETAL)", "pass"), ("A2 STAINLESS", "pass"),
    ("ALUMINUM OR STEEL (SEE NOTE 4)", "warn"), ("TBD", "warn"), ("Unobtainium", "warn"),
    ("SEE PARTS LIST", "refer_bom"), (None, "fail"),
])
def test_classify_material(text, status):
    assert ck.classify_material(text)[0] == status


# ------------------------------------------------------------ rule checks on rows

def title_rows(**vals):
    defaults = {"part_number": "P-1", "material": "6061-T6", "revision": "A", "units": "mm"}
    defaults.update(vals)
    rows = []
    for k, v in defaults.items():
        f = field(v, ["x"] if v else [], "high" if v else "low")
        f["verified"] = True if v else None
        rows.append({"row_id": f"tb.{k}", "section": "title", "key": k, "label": k, "fields": {"value": f}})
    return rows


def bom_row(i, item, qty, mat="6061-T6"):
    fs = {k: {**field(v, ["x"]), "verified": True} for k, v in
          {"item_no": item, "part_number": f"P-{i}", "description": "D", "material": mat, "quantity": qty}.items()}
    return {"row_id": f"bom.{i}", "section": "bom", "key": f"bom.{i}", "label": f"Item {item}", "fields": fs}


def dim_row(i, nominal, tol, unit="mm"):
    fs = {"nominal": {**field(nominal, ["x"]), "verified": True}, "tolerance": {**field(tol, ["x"] if tol else []), "verified": True if tol else None}}
    return {"row_id": f"dim.{i}", "section": "dim", "key": f"dim.{i}", "label": f"Dim {i}", "unit": unit, "fields": fs}


def by_id(checks):
    return {c["id"]: c for c in checks}


def test_clean_drawing_passes_everything():
    rows = title_rows() + [bom_row(0, "1", "2"), bom_row(1, "2", "4")] + [dim_row(0, "120.00", "±0.1"), dim_row(1, "Ø8.5", None)]
    checks = ck.run_checks(rows)
    assert all(c["status"] == "pass" for c in checks), [c for c in checks if c["status"] != "pass"]
    ck.apply_statuses(rows, checks)
    assert {r["status"] for r in rows} == {"verified"}


def test_missing_revision_fails_and_marks_row_failed():
    rows = title_rows(revision=None)
    checks = ck.run_checks(rows)
    assert by_id(checks)["required.revision"]["status"] == "fail"
    ck.apply_statuses(rows, checks)
    rev = next(r for r in rows if r["key"] == "revision")
    assert rev["status"] == "failed"


def test_zero_quantity_fails():
    rows = title_rows() + [bom_row(0, "1", "1"), bom_row(1, "2", "0")]
    checks = ck.run_checks(rows)
    assert by_id(checks)["qty.bom.1"]["status"] == "fail"
    assert "qty.bom.0" not in by_id(checks)
    ck.apply_statuses(rows, checks)
    assert rows[-1]["status"] == "failed"
    assert rows[-1]["fields"]["quantity"]["status"] == "failed"
    assert rows[-1]["fields"]["part_number"]["status"] == "verified"


def test_duplicate_item_numbers():
    rows = title_rows() + [bom_row(0, "1", "1"), bom_row(1, "1", "2")]
    checks = ck.run_checks(rows)
    dup = by_id(checks)["dup.1"]
    assert dup["status"] == "fail" and len(dup["targets"]) == 2


def test_ambiguous_material_warns():
    rows = title_rows(material="ALUMINUM OR STEEL")
    checks = ck.run_checks(rows)
    assert by_id(checks)["material.title"]["status"] == "warn"


def test_see_parts_list_passes_when_bom_has_materials():
    rows = title_rows(material="SEE PARTS LIST") + [bom_row(0, "1", "1", "AISI 304")]
    assert by_id(ck.run_checks(rows))["material.title"]["status"] == "pass"


def test_inch_dimension_on_mm_drawing_warns():
    rows = title_rows() + [dim_row(0, '2.50"', None, "inch")]
    checks = ck.run_checks(rows)
    assert by_id(checks)["unit.dim.0"]["status"] == "warn"


def test_bad_nominal_and_tolerance():
    rows = title_rows() + [dim_row(0, "SEE MODEL", "about 0.1")]
    c = by_id(ck.run_checks(rows))
    assert c["dimnum.dim.0"]["status"] == "fail"
    assert c["tol.dim.0"]["status"] == "warn"


def test_low_confidence_and_unverified_need_review():
    rows = title_rows()
    rows[0]["fields"]["value"]["confidence"] = "low"
    rows[1]["fields"]["value"]["verified"] = False
    rows[1]["fields"]["value"]["verify_msg"] = "Value not found"
    checks = ck.run_checks(rows)
    assert by_id(checks)["source"]["status"] == "warn"
    ck.apply_statuses(rows, checks)
    assert rows[0]["status"] == "needs_review"
    assert rows[1]["status"] == "needs_review"


def test_human_edit_overrides_confidence_but_not_checks():
    rows = title_rows() + [bom_row(0, "1", "0")]
    q = rows[-1]["fields"]["quantity"]
    q.update(confidence="low", human=True)
    checks = ck.run_checks(rows)
    ck.apply_statuses(rows, checks)
    assert rows[-1]["status"] == "failed"  # quantity 0 is still wrong, even if typed by a human
    q["value"] = "3"
    checks = ck.run_checks(rows)
    ck.apply_statuses(rows, checks)
    assert rows[-1]["status"] == "verified"
