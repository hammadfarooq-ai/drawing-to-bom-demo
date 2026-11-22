"""Orchestrates one run: render -> spans -> extract -> verify -> checks -> save."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Callable

from . import checks as ck
from . import extract, pdf_utils
from .audit import AuditLog, now_iso, safe_stem

STAGES = ["rendering", "reading_text", "extracting", "checking", "done"]

_EMPTY_FIELD = {"value": None, "source_span_ids": [], "confidence": "low", "reason": "Not returned by model"}


def _field(raw) -> dict:
    if not isinstance(raw, dict):
        return dict(_EMPTY_FIELD)
    f = {**_EMPTY_FIELD, **{k: raw.get(k, _EMPTY_FIELD[k]) for k in _EMPTY_FIELD}}
    if f["value"] is not None:
        f["value"] = str(f["value"]).strip() or None
    if f["confidence"] not in ("high", "medium", "low"):
        f["confidence"] = "low"
    f["human"] = False
    return f


def build_rows(model_json: dict, span_index: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    tb = model_json.get("title_block") or {}
    for key, label in extract.TITLE_BLOCK_KEYS:
        f = ck.verify_field(_field(tb.get(key)), span_index, key)
        rows.append({"row_id": f"tb.{key}", "section": "title", "key": key, "label": label, "fields": {"value": f}})
    for i, line in enumerate(model_json.get("bom_lines") or []):
        fields = {k: ck.verify_field(_field((line or {}).get(k)), span_index, k) for k in extract.BOM_KEYS}
        item = fields["item_no"]["value"] or str(i + 1)
        rows.append({"row_id": f"bom.{i}", "section": "bom", "key": f"bom.{i}", "label": f"Item {item}", "fields": fields})
    for i, d in enumerate(model_json.get("dimensions") or []):
        d = d or {}
        fields = {k: ck.verify_field(_field(d.get(k)), span_index, k) for k in ("nominal", "tolerance")}
        unit = d.get("unit") if d.get("unit") in ("mm", "inch", "deg") else None
        rows.append({"row_id": f"dim.{i}", "section": "dim", "key": f"dim.{i}",
                     "label": str(d.get("feature") or f"Dimension {i + 1}")[:80], "unit": unit, "fields": fields})
    for i, n in enumerate(model_json.get("notes") or []):
        n = n or {}
        cat = n.get("category") if n.get("category") in extract.NOTE_CATEGORIES else "general"
        rows.append({"row_id": f"note.{i}", "section": "note", "key": f"note.{i}",
                     "label": cat.replace("_", " ").capitalize(), "fields": {"text": ck.verify_field(_field(n.get("text")), span_index, "text")}})
    return rows


def evaluate(result: dict) -> None:
    """(Re)run checks and statuses on a result in place."""
    result["checks"] = ck.run_checks(result["rows"])
    ck.apply_statuses(result["rows"], result["checks"])
    result["summary"] = summarize(result)


def summarize(result: dict) -> dict:
    rows = result["rows"]
    counts = {"verified": 0, "needs_review": 0, "failed": 0}
    for r in rows:
        counts[r["status"]] += 1
    chk = {"pass": 0, "warn": 0, "fail": 0}
    for c in result["checks"]:
        chk[c["status"]] += 1
    reviewed = sum(1 for r in rows if r.get("review"))
    return {"rows": counts, "checks": chk, "reviewed": reviewed, "total": len(rows)}


def process(pdf_src: Path, original_name: str, run_dir: Path, set_stage: Callable[[str], None],
            model_call=extract.call_model) -> dict:
    log = AuditLog(run_dir)
    log.log("run_started", file=original_name)
    pdf_path = run_dir / (safe_stem(original_name) + ".pdf")
    if pdf_src.resolve() != pdf_path.resolve():
        shutil.copyfile(pdf_src, pdf_path)
    log.log("pdf_saved", path=pdf_path.name, bytes=pdf_path.stat().st_size)

    set_stage("rendering")
    doc = pdf_utils.open_pdf(pdf_path)
    page_count = doc.page_count
    pages = pdf_utils.render_pages(doc, run_dir / "pages")
    log.log("pages_rendered", count=len(pages), total_pages=page_count, dpi=pdf_utils.RENDER_DPI)

    set_stage("reading_text")
    spans, warnings = pdf_utils.extract_spans(doc, pages)
    doc.close()
    page_dicts = [p.to_dict() for p in pages]
    log.save_json("spans.json", {"pages": page_dicts, "spans": spans})
    log.log("spans_extracted", count=len(spans), sources={p.page: p.text_source for p in pages}, warnings=warnings)
    if page_count > len(pages):
        warnings.append(f"Only the first {len(pages)} of {page_count} pages were processed.")
    if not spans:
        warnings.append("No text could be read from this drawing, so no values can be source-verified.")

    set_stage("extracting")
    model_json, raw = model_call([p.png_path for p in pages], spans, page_dicts, log=log.log)
    log.save_text("model_response_raw.json", raw)
    log.log("extraction_complete", model=extract.get_model(),
            bom_lines=len(model_json.get("bom_lines") or []), dimensions=len(model_json.get("dimensions") or []))

    set_stage("checking")
    span_index = {s["id"]: s for s in spans}
    result = {
        "run_id": run_dir.name,
        "filename": original_name,
        "created": now_iso(),
        "model": extract.get_model(),
        "pages": page_dicts,
        "warnings": warnings,
        "rows": build_rows(model_json, span_index),
    }
    evaluate(result)
    log.save_json("checks.json", result["checks"])
    log.save_json("bom.json", result)
    log.log("checks_complete", **result["summary"])
    set_stage("done")
    return result


def apply_review(result: dict, run_dir: Path, row_id: str, action: str, changes: dict | None, note: str | None) -> dict:
    row = next((r for r in result["rows"] if r["row_id"] == row_id), None)
    if row is None:
        raise KeyError(row_id)
    log = AuditLog(run_dir)
    ts = now_iso()
    if action == "edit":
        edits = []
        for name, new in (changes or {}).items():
            if name not in row["fields"]:
                continue
            f = row["fields"][name]
            new = None if new is None or str(new).strip() == "" else str(new).strip()
            old = f.get("value")
            if new == old:
                continue
            if "original" not in f:
                f["original"] = copy.deepcopy({k: f.get(k) for k in ("value", "confidence", "verified", "verify_msg")})
            f.update({"value": new, "human": True, "verified": True, "verify_msg": "Entered by reviewer"})
            edits.append({"field": name, "old": old, "new": new})
            log.log("human_edit", row_id=row_id, row=row["label"], field=name, old_value=old, new_value=new, time=ts)
        row["review"] = {"state": "edited", "at": ts, "edits": edits}
    elif action == "approve":
        row["review"] = {"state": "approved", "at": ts, "status_at_approval": row["status"]}
        log.log("human_approve", row_id=row_id, row=row["label"], status_at_approval=row["status"],
                values={k: f.get("value") for k, f in row["fields"].items()}, note=note, time=ts)
    elif action == "reset":
        row.pop("review", None)
        log.log("human_reset", row_id=row_id, row=row["label"], time=ts)
    else:
        raise ValueError(action)
    evaluate(result)
    log.save_json("checks.json", result["checks"])
    log.save_json("bom.json", result)
    return result
