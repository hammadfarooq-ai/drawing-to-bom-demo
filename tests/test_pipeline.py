"""End-to-end pipeline on the sample PDFs with a fake model (no API calls)."""
import json
from pathlib import Path

import pytest

from app import extract, pipeline
from fake_model import response_for

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def fake_call(sample_name):
    def call(pngs, spans, pages, log=lambda *a, **k: None):
        data = response_for(sample_name, spans)
        return data, json.dumps(data)
    return call


def run(sample, tmp_path):
    run_dir = tmp_path / sample.replace(".pdf", "")
    run_dir.mkdir()
    stages = []
    result = pipeline.process(SAMPLES / sample, sample, run_dir, stages.append, model_call=fake_call(sample))
    return result, run_dir, stages


def rows(result, section):
    return [r for r in result["rows"] if r["section"] == section]


def checks(result):
    return {c["id"]: c for c in result["checks"]}


@pytest.fixture(scope="module", autouse=True)
def samples_exist():
    if not (SAMPLES / "01_bracket_BRK-1001.pdf").exists():
        pytest.skip("run scripts/make_samples.py first")


def test_bracket_all_verified(tmp_path):
    result, run_dir, stages = run("01_bracket_BRK-1001.pdf", tmp_path)
    assert stages == ["rendering", "reading_text", "extracting", "checking", "done"]
    for name in ["01_bracket_BRK-1001.pdf", "pages/page_1.png", "spans.json", "model_response_raw.json", "bom.json", "checks.json", "audit_log.jsonl"]:
        assert (run_dir / name).exists(), name
    # every cited value resolves to a real box
    for r in result["rows"]:
        for f in r["fields"].values():
            if f["value"] is not None:
                assert f["verified"] is True, (r["label"], f)
                assert f["boxes"]
    assert result["summary"]["checks"]["fail"] == 0
    assert result["summary"]["rows"]["failed"] == 0


def test_assembly_bom(tmp_path):
    result, _, _ = run("02_assembly_ASM-2040.pdf", tmp_path)
    bom = rows(result, "bom")
    assert [b["fields"]["part_number"]["value"] for b in bom] == ["BRK-2041", "BRK-2042", "SHF-2043", "ROL-2044", "SCR-M6-16"]
    assert all(b["status"] == "verified" for b in bom)
    c = checks(result)
    assert c["qty"]["status"] == "pass" and c["dup"]["status"] == "pass"
    assert c["material.title"]["status"] == "pass"  # "SEE PARTS LIST" resolved via BOM


def test_problem_drawing_flags(tmp_path):
    result, run_dir, _ = run("03_problem_SMP-3307.pdf", tmp_path)
    c = checks(result)
    assert c["required.revision"]["status"] == "fail"
    assert c["qty.bom.1"]["status"] == "fail"
    assert c["material.title"]["status"] == "warn"
    assert any(k.startswith("unit.dim.") for k in c)
    tb = {r["key"]: r for r in rows(result, "title")}
    assert tb["revision"]["status"] == "failed"
    assert tb["material"]["status"] == "needs_review"
    assert rows(result, "bom")[1]["status"] == "failed"

    # reviewer fixes the quantity -> check re-runs and passes; edit is audited
    result = pipeline.apply_review(result, run_dir, "bom.1", "edit", {"quantity": "4"}, None)
    assert "qty.bom.1" not in checks(result)
    assert rows(result, "bom")[1]["status"] == "verified"
    assert result["summary"]["reviewed"] == 1
    log = [json.loads(l) for l in (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    edit = next(e for e in log if e["event"] == "human_edit")
    assert edit["old_value"] == "0" and edit["new_value"] == "4" and edit["field"] == "quantity"

    result = pipeline.apply_review(result, run_dir, "tb.material", "approve", None, None)
    assert result["summary"]["reviewed"] == 2
    saved = json.loads((run_dir / "bom.json").read_text(encoding="utf-8"))
    assert saved["summary"]["reviewed"] == 2


def test_hallucinated_span_is_unverified(tmp_path):
    def bad_call(pngs, spans, pages, log=None):
        data = response_for("01", spans)
        data["title_block"]["material"] = {"value": "7075-T6", "source_span_ids": data["title_block"]["material"]["source_span_ids"], "confidence": "high", "reason": ""}
        data["title_block"]["finish"] = {"value": "CLEAR ANODIZE", "source_span_ids": ["p1_s999"], "confidence": "high", "reason": ""}
        return data, json.dumps(data)
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    result = pipeline.process(SAMPLES / "01_bracket_BRK-1001.pdf", "x.pdf", run_dir, lambda s: None, model_call=bad_call)
    tb = {r["key"]: r for r in rows(result, "title")}
    assert tb["material"]["fields"]["value"]["verified"] is False
    assert tb["material"]["status"] == "needs_review"
    assert tb["finish"]["fields"]["value"]["verified"] is False
    assert checks(result)["source"]["status"] == "warn"


def test_missing_api_key_gives_readable_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(extract.ExtractionError, match="No OpenAI API key"):
        extract.call_model([], [], [])


def test_schema_is_strict_compatible():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(extract.build_schema())


def test_api_rejects_non_pdf_and_lists_samples():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/runs/upload", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400 and "PDF" in r.json()["error"]
    assert "01_bracket_BRK-1001.pdf" in client.get("/api/samples").json()["samples"]
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404
