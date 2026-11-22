"""FastAPI routes for the Drawing -> BOM demo."""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
import traceback
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import extract, pdf_utils, pipeline
from .audit import RUNS_DIR, ROOT, AuditLog, new_run_dir, valid_run_id

load_dotenv(ROOT / ".env")
log = logging.getLogger("bom")

SAMPLES_DIR = ROOT / "samples"
STATIC_DIR = ROOT / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Drawing -> BOM Demo")
RUNS_DIR.mkdir(exist_ok=True)

# In-memory job status; results themselves live on disk in runs/<id>/bom.json
JOBS: dict[str, dict] = {}
_result_lock = threading.Lock()


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.error("Unhandled error: %s", traceback.format_exc())
    return JSONResponse(status_code=500, content={"error": "Something went wrong on the server. See the server console for details."})


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# ------------------------------------------------------------------ pages

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/config")
def config():
    import os
    ocr_ok, ocr_msg = pdf_utils.ocr_available()
    key = os.getenv("OPENAI_API_KEY", "").strip()
    return {
        "has_api_key": bool(key) and not key.startswith("sk-your"),
        "model": extract.get_model(),
        "ocr_available": ocr_ok,
        "ocr_message": None if ocr_ok else ocr_msg,
    }


@app.get("/api/samples")
def samples():
    SAMPLES_DIR.mkdir(exist_ok=True)
    return {"samples": sorted(p.name for p in SAMPLES_DIR.glob("*.pdf"))}


# ------------------------------------------------------------------ runs

def _run_job(run_dir: Path, pdf_src: Path, name: str, cleanup: bool):
    run_id = run_dir.name

    def set_stage(stage: str):
        JOBS[run_id]["stage"] = stage

    try:
        pipeline.process(pdf_src, name, run_dir, set_stage)
        JOBS[run_id]["state"] = "done"
    except (pdf_utils.PdfError, extract.ExtractionError) as exc:
        JOBS[run_id].update(state="error", error=str(exc))
        AuditLog(run_dir).log("run_failed", error=str(exc))
    except Exception as exc:  # never leak a stack trace to the UI
        log.error("Run %s failed: %s", run_id, traceback.format_exc())
        JOBS[run_id].update(state="error", error=f"Unexpected error while processing the drawing ({type(exc).__name__}). See the server console.")
        AuditLog(run_dir).log("run_failed", error=repr(exc))
    finally:
        if cleanup:
            pdf_src.unlink(missing_ok=True)


def _start(pdf_src: Path, name: str, cleanup: bool) -> dict:
    run_dir = new_run_dir(name)
    JOBS[run_dir.name] = {"state": "running", "stage": "queued", "error": None}
    threading.Thread(target=_run_job, args=(run_dir, pdf_src, name, cleanup), daemon=True).start()
    return {"run_id": run_dir.name}


@app.post("/api/runs/upload")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "drawing.pdf").name
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 25 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That file isn't a PDF. Please upload a PDF drawing.")
    RUNS_DIR.mkdir(exist_ok=True)
    tmp = RUNS_DIR / f".upload_{uuid.uuid4().hex}.pdf"
    tmp.write_bytes(data)
    return _start(tmp, name, cleanup=True)


class SampleReq(BaseModel):
    name: str


@app.post("/api/runs/sample")
def run_sample(req: SampleReq):
    path = (SAMPLES_DIR / Path(req.name).name)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise HTTPException(404, f"Sample '{req.name}' not found.")
    return _start(path, path.name, cleanup=False)


def _check_id(run_id: str):
    if not valid_run_id(run_id):
        raise HTTPException(404, "Run not found.")


@app.get("/api/runs/{run_id}/status")
def status(run_id: str):
    _check_id(run_id)
    job = JOBS.get(run_id)
    if job is None:  # e.g. server restarted
        done = (RUNS_DIR / run_id / "bom.json").exists()
        return {"state": "done" if done else "error", "stage": "done", "error": None if done else "This run did not finish."}
    return job


def _load(run_id: str) -> dict:
    p = RUNS_DIR / run_id / "bom.json"
    if not p.exists():
        raise HTTPException(404, "Result not available for this run.")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    _check_id(run_id)
    return _load(run_id)


@app.get("/api/runs/{run_id}/pages/{page}")
def page_image(run_id: str, page: int):
    _check_id(run_id)
    p = RUNS_DIR / run_id / "pages" / f"page_{page}.png"
    if not p.exists():
        raise HTTPException(404, "Page not found.")
    return FileResponse(p, media_type="image/png")


class ReviewReq(BaseModel):
    row_id: str
    action: str  # approve | edit | reset
    changes: dict[str, str | None] | None = None
    note: str | None = None


@app.post("/api/runs/{run_id}/review")
def review(run_id: str, req: ReviewReq):
    _check_id(run_id)
    if req.action not in ("approve", "edit", "reset"):
        raise HTTPException(400, "Unknown review action.")
    with _result_lock:
        result = _load(run_id)
        try:
            return pipeline.apply_review(result, RUNS_DIR / run_id, req.row_id, req.action, req.changes, req.note)
        except KeyError:
            raise HTTPException(404, "Row not found.")


@app.get("/api/runs/{run_id}/audit")
def audit(run_id: str):
    _check_id(run_id)
    return {"events": AuditLog(RUNS_DIR / run_id).read()}


@app.get("/api/runs/{run_id}/export.csv")
def export_csv(run_id: str):
    _check_id(run_id)
    result = _load(run_id)
    tb = {r["key"]: r["fields"]["value"]["value"] for r in result["rows"] if r["section"] == "title"}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["parent_part_number", "parent_revision", "item_no", "part_number", "description", "material", "quantity", "status", "review"])
    bom = [r for r in result["rows"] if r["section"] == "bom"]
    if bom:
        for r in bom:
            f = r["fields"]
            w.writerow([tb.get("part_number"), tb.get("revision"), f["item_no"]["value"], f["part_number"]["value"],
                        f["description"]["value"], f["material"]["value"], f["quantity"]["value"],
                        r["status"], (r.get("review") or {}).get("state", "")])
    else:  # single part drawing: export the part itself as one line
        w.writerow([tb.get("part_number"), tb.get("revision"), "1", tb.get("part_number"), tb.get("part_name"),
                    tb.get("material"), "1", "", ""])
    AuditLog(RUNS_DIR / run_id).log("export", format="csv")
    fname = f"{run_id}_bom.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/runs/{run_id}/export.json")
def export_json(run_id: str):
    _check_id(run_id)
    result = _load(run_id)
    result["audit_log"] = AuditLog(RUNS_DIR / run_id).read()
    AuditLog(RUNS_DIR / run_id).log("export", format="json")
    fname = f"{run_id}_result.json"
    return Response(json.dumps(result, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
