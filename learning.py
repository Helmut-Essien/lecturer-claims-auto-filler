"""Local 'learning' memory: past clerk corrections -> name aliases + prompt hints.

No model is fine-tuned. We simply remember what the clerk corrected and feed it back
(as aliases for roster matching and as examples in the extraction prompt).
"""
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

from roster import norm_name

DIR = Path(__file__).resolve().parent / "learning"
CORR = DIR / "corrections.json"
EXTRA = DIR / "extra_lecturers.json"
FIELDS = (("lecturer_name", "raw_name"), ("course_code", "raw_course"),
          ("time_in", "raw_time_in"), ("time_out", "raw_time_out"))


def _txt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path, data):
    DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def corrections() -> list:
    return _load(CORR, [])


def record(df, batch_date) -> int:
    """Store every approved row whose final values differ from what the model read."""
    new = []
    for r in df.to_dict("records"):
        if not _txt(r.get("raw_name")):
            continue  # manual row: nothing was read
        diffs = {}
        for final, raw in FIELDS:
            a, b = _txt(r.get(raw)), _txt(r.get(final))
            if a and a != b:
                diffs[final] = {"read": a, "final": b}
        # Only learn a name alias if the clerk set the name, or the roster match was unflagged.
        clerk_set = _txt(r.get("lecturer_name")) not in (_txt(r.get("raw_name")), _txt(r.get("name_auto")))
        auto_ok = _txt(r.get("match_method")) not in ("", "none") and "roster:" not in _txt(r.get("check"))
        if "lecturer_name" in diffs and not (clerk_set or auto_ok):
            del diffs["lecturer_name"]
        if diffs:
            matched = _txt(r.get("matched_name")) == _txt(r.get("lecturer_name"))
            new.append({
                "bio_id": _txt(r.get("matched_bio_id")) if matched else "",
                "canonical_name": _txt(r.get("lecturer_name")),
                "raw_name_read": _txt(r.get("raw_name")),
                "raw_course": _txt(r.get("raw_course")),
                "final_course": _txt(r.get("course_code")),
                "raw_bio_read": _txt(r.get("bio_id_hint")),
                "diffs": diffs, "date": str(batch_date),
                "source_file": _txt(r.get("source_file")),
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            })
    if new:
        _save(CORR, corrections() + new)
    return len(new)


def aliases() -> dict:
    """normalized misread name -> canonical name (latest correction wins)."""
    out = {}
    for c in corrections():
        d = c["diffs"].get("lecturer_name")
        if d and norm_name(d["read"]):
            out[norm_name(d["read"])] = d["final"]
    return out


def course_fixes() -> dict:
    out = {}
    for c in corrections():
        d = c["diffs"].get("course_code")
        if d:
            out[d["read"].upper()] = d["final"]
    return out


def hints(limit: int = 40) -> list:
    """Most frequent (then most recent) correction examples, phrased for the prompt."""
    counts, order = Counter(), {}
    for i, c in enumerate(corrections()):
        for field, d in c["diffs"].items():
            if field == "lecturer_name":
                h = f'name read as "{d["read"]}" is actually "{d["final"]}"'
            elif field == "course_code":
                h = f'course read as "{d["read"]}" is actually "{d["final"]}"'
            else:
                h = f'{field.replace("_", " ")} read as {d["read"]} was actually {d["final"]}'
            counts[h] += 1
            order[h] = i
    return sorted(counts, key=lambda h: (-counts[h], -order[h]))[:limit]


def stats() -> dict:
    corr = corrections()
    names = Counter(c["canonical_name"] for c in corr if "lecturer_name" in c["diffs"])
    return {"corrections": len(corr), "aliases": len(aliases()),
            "top": names.most_common(5), "extras": len(extras())}


def extras() -> list:
    return _load(EXTRA, [])


def add_extra(name: str, bio_id: str = "") -> bool:
    cur = extras()
    if any(norm_name(e["name"]) == norm_name(name) for e in cur):
        return False
    _save(EXTRA, cur + [{"name": name.strip(), "bio_id": bio_id.strip(),
                         "added": datetime.now().isoformat(timespec="seconds")}])
    return True


def reset():
    for p in (CORR, EXTRA):
        p.unlink(missing_ok=True)
