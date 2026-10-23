"""Optional course list: canonical course codes + matching of handwritten codes."""
import re
from difflib import SequenceMatcher

import pandas as pd

import roster as rs

CODE_HEADERS = {"coursecode", "code", "course", "coursecodes", "courseno", "coursenumber", "courseid"}
TITLE_HEADERS = {"coursetitle", "title", "coursename", "description", "name", "subject"}
CODE_RE = re.compile(r"[A-Z]{2,6}\s?\d{3}[A-Z]?")
SURE = 0.85  # codes are short, so be stricter than names
COURSE_FLOOR = 0.5  # below this a code is "course not in list"


def read_file(uploaded) -> pd.DataFrame:
    return rs.read_file(uploaded, need=(CODE_HEADERS,))


def guess_columns(df):
    key = {c: rs._key(c) for c in df.columns}
    code = next((c for c in df.columns if key[c] in CODE_HEADERS), None)
    title = next((c for c in df.columns if key[c] in TITLE_HEADERS and c != code), None)
    return code, title


def norm_code(s) -> str:
    """'mps414' / 'MPS  414' -> 'MPS 414'."""
    s = re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper())
    s = re.sub(r"([A-Z])(\d)", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def split_codes(s):
    """All course-code-looking parts of a cell, plus leftover suffix tokens (e.g. EVE)."""
    n = norm_code(s)
    codes = [norm_code(c) for c in CODE_RE.findall(n)]
    rest = CODE_RE.sub(" ", n).split()
    return codes or ([n] if n else []), rest


def build(df, code_col, title_col=None) -> pd.DataFrame:
    out = pd.DataFrame({"code": df[code_col].map(norm_code),
                        "title": df[title_col].astype(str).str.strip() if title_col else ""})
    out = out[out.code.str.match(CODE_RE)]
    return out.drop_duplicates("code").reset_index(drop=True)


def _score(a, b) -> float:
    if a == b:
        return 1.0
    la, da = a.split()[0], "".join(a.split()[1:])
    lb, db = b.split()[0], "".join(b.split()[1:])
    return 0.5 * SequenceMatcher(None, la, lb).ratio() + 0.5 * SequenceMatcher(None, da, db).ratio()


def match_one(raw, courses, aliases=None):
    """Return dict(code, method, score, alt, note); code == '' means unmatched."""
    none = {"code": "", "method": "none", "score": 0.0, "alt": "", "note": ""}
    if aliases and str(raw).upper().strip() in aliases:
        return {**none, "code": aliases[str(raw).upper().strip()], "method": "learned", "score": 1.0}
    codes, rest = split_codes(raw)
    if courses is None or not len(courses) or not codes:
        return none
    best = None
    for c in codes:
        sims = courses.code.map(lambda k: _score(c, k)).sort_values(ascending=False, kind="stable")
        cand = (float(sims.iloc[0]), courses.loc[sims.index[0], "code"],
                courses.loc[sims.index[1], "code"] if len(sims) > 1 else "",
                float(sims.iloc[1]) if len(sims) > 1 else 0.0)
        best = max(best or cand, cand)
    score, code, alt, alt_s = best
    if score < COURSE_FLOOR:
        return {**none, "score": round(score, 2), "alt": code, "note": "course not in list"}
    notes = []
    if score < SURE:
        notes.append("low match")
    if score - alt_s < rs.TIE:
        notes.append("close tie")
    if len(codes) > 1:
        notes.append(f"cell has {len(codes)} codes ({' / '.join(codes)})")
        alt = next((c for c in codes if c != code), alt)
    note = f"course: {', '.join(notes)} - check (alt: {alt})" if notes else ""
    return {"code": code, "method": "exact" if score == 1 else "fuzzy", "score": round(score, 2),
            "alt": alt, "note": note, "suffix": " ".join(rest)}


def apply_courses(df: pd.DataFrame, courses, aliases=None) -> pd.DataFrame:
    df = df.copy()
    df["alt_course"], df["course_method"], df["course_score"] = "", "", 0.0
    if "course_auto" not in df:
        df["course_auto"] = ""
    if (courses is None or not len(courses)) and not aliases or not len(df):
        return df
    codes = set(courses.code) if courses is not None else set()
    for i, row in df.iterrows():
        cur, raw = rs._txt(row.course_code), rs._txt(row.get("raw_course"))
        auto = bool(raw) and cur in (raw, rs._txt(row.get("course_auto")))
        if not auto and norm_code(cur) in codes:
            m = {"code": norm_code(cur), "method": "clerk", "score": 1.0, "alt": "", "note": ""}
        else:
            m = match_one(raw if auto else cur, courses, aliases)
        if auto and m["code"]:
            df.loc[i, ["course_code", "course_auto"]] = [m["code"], m["code"]]
        df.loc[i, ["alt_course", "course_method"]] = [m["alt"], m["method"]]
        df.loc[i, "course_score"] = m["score"]
        if m["note"]:
            df.loc[i, "check"] = "; ".join(x for x in [df.loc[i, "check"], m["note"]] if x)
    return df
