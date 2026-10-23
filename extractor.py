"""Gemma 4 multimodal extraction of register pages via the Gemini API or a local Ollama."""
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request

from google import genai
from google.genai import errors, types
from PIL import Image

MODEL = os.getenv("GEMMA_MODEL", "gemma-4-31b-it")
BACKEND = os.getenv("LLM_BACKEND", "gemini").lower()  # gemini | ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4-e4b-vision")

PROMPT = """You are reading a photographed handwritten lecturer attendance register page.
The page has a handwritten date, usually at the top but sometimes written at the bottom
or after the last row (e.g. "23rd October 2026"). Below, lecturers sign in per course with:
course code, lecturer name, bio ID, time in, time out, signature.

Return ONLY a JSON object, no markdown, in exactly this shape:
{"sheet_date": "YYYY-MM-DD" or null,
 "entries": [{"course_code": str, "lecturer_name": str (exactly as written),
              "roster_guess": str or null, "bio_id_hint": str or null,
              "time_in": "HH:MM", "time_out": "HH:MM", "confidence": 0.0-1.0, "notes": str}]}

Rules:
- One entry per lecturer row. A course code written once may apply to several rows below it.
- Normalize ALL times to 24-hour HH:MM ("8am"->"08:00", "0800"->"08:00", "2.30pm"->"14:30").
  Times are same-day; infer am/pm sensibly for teaching hours (07:00-22:00).
  Read each row's am/pm marker carefully ("am" vs "pm" are easily confused). If time_out has no
  marker, choose the reading that is AFTER time_in and gives a plausible class (usually 1-4 hours).
  If a value is ambiguous, still give your best guess, lower confidence and explain in notes.
- Copy course codes and names exactly as written; do not merge two rows into one.
- Ignore signatures entirely.
- confidence = your certainty for that row. Put any doubt in notes (else "").
- If the sheet date is unreadable, use null.
- roster_guess: null unless a KNOWN LECTURERS list is given below.
"""


def build_prompt(roster_names=None, hints=None, course_codes=None) -> str:
    """Base prompt + optional roster names, course list and learned handwriting hints."""
    p = PROMPT
    if roster_names:
        p += ("\nKNOWN LECTURERS (official roster, name (bio ID)):\n" + "; ".join(roster_names)
              + "\nFor each row, still transcribe lecturer_name exactly as written, and set "
              "roster_guess to the roster name (copied EXACTLY from this list) that the handwriting "
              "most plausibly is. Handwriting is messy: allow misspellings, missing middle names, "
              "initials, swapped first/last names and missing titles. Use null only if no roster "
              "name is plausible at all.\n")
    if course_codes:
        p += ("\nKNOWN COURSE CODES: " + ", ".join(course_codes)
              + "\nTranscribe course_code as written; codes on the page are usually one of these.\n")
    if hints:
        p += ("\nHANDWRITING NOTES learned from the clerk's past corrections on this register:\n- "
              + "\n- ".join(hints) + "\n")
    return p


_CLIENT = None


def _client():
    # Keep one client alive; a temporary one can be garbage-collected (closing
    # its HTTP session) before the request is sent.
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _CLIENT


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Model did not return JSON")
    return json.loads(m.group(0))


class ExtractionError(Exception):
    """Raised when a page cannot be read or parsed."""


def extract_page(image_bytes: bytes, filename: str, prompt: str | None = None,
                 backend: str | None = None, model: str | None = None) -> dict:
    """Return {"sheet_date", "entries"[...]} for one image. Raises ExtractionError."""
    try:
        return _extract(image_bytes, filename, prompt or PROMPT, backend or BACKEND, model)
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"{type(e).__name__}: {e}") from e


extract_attendance = extract_page


def _retry(call, attempts=3):
    """Run call(), retrying twice with backoff on transient 5xx server errors."""
    for i in range(attempts):
        try:
            return call()
        except (errors.ServerError, urllib.error.HTTPError) as e:
            if getattr(e, "code", 500) < 500 or i == attempts - 1:
                raise
            time.sleep(3 * (i + 1))


def _gemini(jpeg: bytes, prompt: str, model: str | None) -> str:
    resp = _retry(lambda: _client().models.generate_content(
        model=model or MODEL,
        contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt],
        config=types.GenerateContentConfig(temperature=0.1)))
    return resp.text or ""


def _ollama(jpeg: bytes, prompt: str, model: str | None) -> str:
    body = json.dumps({
        "model": model or OLLAMA_MODEL, "stream": False, "format": "json", "think": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
        "messages": [{"role": "user", "content": prompt,
                      "images": [base64.b64encode(jpeg).decode()]}],
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_HOST}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})

    def call():
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["message"]["content"]
    return _retry(call)


def _extract(image_bytes: bytes, filename: str, prompt: str, backend: str, model) -> dict:
    img = Image.open(io.BytesIO(image_bytes))
    img.verify()  # catches corrupt files early
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((2000, 2000))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    run = _ollama if backend == "ollama" else _gemini
    data = _parse_json(run(buf.getvalue(), prompt, model))
    entries = []
    for e in data.get("entries") or []:
        name = str(e.get("lecturer_name") or "").strip()
        course = str(e.get("course_code") or "").strip().upper()
        t_in, t_out = str(e.get("time_in") or "").strip(), str(e.get("time_out") or "").strip()
        entries.append({
            "lecturer_name": name, "course_code": course, "time_in": t_in, "time_out": t_out,
            "raw_name": name, "raw_course": course, "raw_time_in": t_in, "raw_time_out": t_out,
            "roster_guess": str(e.get("roster_guess") or "").strip(),
            "confidence": float(e.get("confidence") or 0.0),
            "source_file": filename,
            "bio_id_hint": str(e.get("bio_id_hint") or ""),
            "notes": str(e.get("notes") or ""),
        })
    return {"sheet_date": data.get("sheet_date"), "entries": entries}
