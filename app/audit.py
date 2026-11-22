"""Per-run folder and append-only JSONL audit log."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")
    return (stem or "drawing")[:60]


def new_run_dir(filename: str) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{ts}_{safe_stem(filename)}"
    run_dir, n = RUNS_DIR / base, 1
    while run_dir.exists():
        n += 1
        run_dir = RUNS_DIR / f"{base}-{n}"
    run_dir.mkdir()
    return run_dir


def valid_run_id(run_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", run_id)) and ".." not in run_id and (RUNS_DIR / run_id).is_dir()


class AuditLog:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = run_dir / "audit_log.jsonl"

    def log(self, event: str, **data) -> None:
        rec = {"ts": now_iso(), "event": event, **data}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def save_json(self, name: str, obj) -> Path:
        p = self.run_dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(p)
        return p

    def save_text(self, name: str, text: str) -> Path:
        p = self.run_dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
