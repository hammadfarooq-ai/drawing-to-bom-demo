"""OpenAI extraction: page images + span list in, schema-constrained JSON out.

The model never produces coordinates. It cites span IDs, and the backend
resolves those IDs to exact boxes (see checks.verify_field).
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

TITLE_BLOCK_KEYS = [
    ("part_number", "Part number"),
    ("part_name", "Part name"),
    ("revision", "Revision"),
    ("material", "Material"),
    ("finish", "Finish / coating"),
    ("general_tolerance", "General tolerance"),
    ("units", "Units"),
    ("scale", "Scale"),
    ("drawn_by", "Drawn by"),
    ("date", "Date"),
]
BOM_KEYS = ["item_no", "part_number", "description", "material", "quantity"]
NOTE_CATEGORIES = ["heat_treatment", "thread", "surface_finish", "hole_callout", "finish", "general"]

MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_S = 120


class ExtractionError(Exception):
    """Raised with a user-readable message."""


def _field_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "source_span_ids", "confidence", "reason"],
        "properties": {
            "value": {"type": ["string", "null"]},
            "source_span_ids": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reason": {"type": "string"},
        },
    }


def build_schema() -> dict:
    f = _field_schema()
    obj = lambda props: {  # noqa: E731
        "type": "object",
        "additionalProperties": False,
        "required": list(props.keys()),
        "properties": props,
    }
    return obj({
        "title_block": obj({k: f for k, _ in TITLE_BLOCK_KEYS}),
        "bom_lines": {"type": "array", "items": obj({k: f for k in BOM_KEYS})},
        "dimensions": {"type": "array", "items": obj({
            "feature": {"type": "string"},
            "nominal": f,
            "tolerance": f,
            "unit": {"type": "string", "enum": ["mm", "inch", "deg", "unknown"]},
        })},
        "notes": {"type": "array", "items": obj({
            "category": {"type": "string", "enum": NOTE_CATEGORIES},
            "text": f,
        })},
    })


SYSTEM_PROMPT = """You extract data from mechanical engineering drawings for a procurement team.

You receive rendered page image(s) and the complete list of text spans found on the drawing. Each span line is:
  <span_id> [x=<0-100 from left>, y=<0-100 from top>] <text>

Rules:
- Every value MUST come from the drawing. Copy it verbatim from the span text (keep symbols like Ø, ±, °, R, M, 2X). Do not reformat dates, numbers or names.
- For every non-null value, list in source_span_ids ALL span IDs whose text makes up the value, in reading order. Only use IDs from the list.
- Exception: "units" may be normalized to exactly "mm" or "inch"; cite the span(s) that state the units.
- If a value is not clearly on the drawing (field blank, illegible, or only implied), return value null, source_span_ids [], confidence "low", and say why in reason. Never invent or infer values.
- confidence: "high" = clearly printed and unambiguous; "medium" = readable but some interpretation needed; "low" = ambiguous, conflicting or missing. reason is a short explanation, empty string only when confidence is high.
- title_block: read the title block (usually bottom-right). general_tolerance is the default tolerance text (e.g. "X.X ±0.2"); finish is the coating/finish called out.
- bom_lines: one entry per row of a parts list / BOM table, if one exists; otherwise an empty array. Quantity is copied as printed (even if it looks wrong).
- dimensions: each toleranced or key dimension. feature = short description you write (e.g. "Overall length", "Mounting hole diameter"). nominal = the dimension value as printed (e.g. "120.00", "2X Ø8.50"). tolerance = tolerance as printed (e.g. "±0.10", "+0.1/-0.0", "H7"). If no tolerance is printed next to the dimension (general tolerance applies, or it is a REF/basic dimension), return tolerance value null with confidence "high" and reason "General tolerance applies" (or "Reference dimension") - this is expected, not uncertain. unit = mm / inch / deg as stated or clearly implied by the drawing's units.
- notes: heat treatment, thread specs, surface finish, hole callouts, finish/coating and other numbered notes; text = the note text verbatim (you may omit the leading note number).
"""


def format_spans(spans: list[dict], pages: list[dict]) -> str:
    sizes = {p["page"]: (p["width_pt"], p["height_pt"]) for p in pages}
    lines = []
    for s in spans:
        w, h = sizes[s["page"]]
        x0, y0, x1, y1 = s["bbox"]
        cx, cy = round((x0 + x1) / 2 / w * 100), round((y0 + y1) / 2 / h * 100)
        lines.append(f"{s['id']} [x={cx}, y={cy}] {s['text']}")
    return "\n".join(lines)


def _image_part(png: Path) -> dict:
    b64 = base64.b64encode(png.read_bytes()).decode()
    return {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": "high"}


def get_model() -> str:
    return os.getenv("OPENAI_MODEL", "").strip() or "gpt-6.2-sol"


def call_model(png_paths: list[Path], spans: list[dict], pages: list[dict], log=lambda *a, **k: None) -> tuple[dict, str]:
    """Return (parsed_json, raw_text). Retries transient failures with backoff."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-your"):
        raise ExtractionError("No OpenAI API key configured. Copy .env.example to .env, set OPENAI_API_KEY, and restart the server.")

    import openai
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)
    model = get_model()
    user_content = [{"type": "input_text", "text": "TEXT SPANS:\n" + format_spans(spans, pages)}]
    user_content += [_image_part(p) for p in png_paths]

    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            log("model_request", attempt=attempt, model=model, span_count=len(spans), pages=len(png_paths))
            resp = client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=[{"role": "user", "content": user_content}],
                text={"format": {"type": "json_schema", "name": "drawing_extraction", "schema": build_schema(), "strict": True}},
                max_output_tokens=16000,
            )
            raw = resp.output_text or ""
            if getattr(resp, "status", "completed") == "incomplete":
                last_error = "The model response was cut off before it finished."
                raise ValueError(last_error)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                last_error = "The model returned a response that was not valid JSON."
                raise ValueError(last_error) from exc
            if not isinstance(data, dict) or "title_block" not in data:
                last_error = "The model returned JSON in an unexpected shape."
                raise ValueError(last_error)
            log("model_response", attempt=attempt, output_chars=len(raw),
                usage=getattr(resp, "usage", None) and resp.usage.model_dump())
            return data, raw
        except openai.AuthenticationError as exc:
            raise ExtractionError("OpenAI rejected the API key (401). Check OPENAI_API_KEY in .env.") from exc
        except openai.NotFoundError as exc:
            raise ExtractionError(f"Model '{model}' was not found or isn't available to this API key. Set OPENAI_MODEL in .env.") from exc
        except openai.PermissionDeniedError as exc:
            raise ExtractionError(f"This API key isn't allowed to use model '{model}'. Set OPENAI_MODEL in .env.") from exc
        except openai.BadRequestError as exc:
            msg = getattr(exc, "message", str(exc))
            raise ExtractionError(f"OpenAI rejected the request: {msg[:300]}") from exc
        except openai.APITimeoutError:
            last_error = f"The OpenAI request timed out after {REQUEST_TIMEOUT_S}s."
        except openai.RateLimitError as exc:
            body = str(exc)
            if "insufficient_quota" in body:
                raise ExtractionError("Your OpenAI account has no remaining quota. Check billing on platform.openai.com.") from exc
            last_error = "OpenAI rate limit reached."
        except (openai.APIConnectionError, openai.InternalServerError) as exc:
            last_error = f"Could not reach OpenAI ({type(exc).__name__})."
        except ValueError:
            pass  # last_error already set (junk / truncated output)
        log("model_retry", attempt=attempt, error=last_error)
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 ** attempt)  # 2s, 4s
    raise ExtractionError(f"Extraction failed after {MAX_ATTEMPTS} attempts: {last_error}")
