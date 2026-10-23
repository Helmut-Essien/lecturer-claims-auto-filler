"""Lecturer roster: load an Excel/CSV list and match extracted rows by bio ID / name."""
import re
from difflib import SequenceMatcher

import pandas as pd

BIO_HEADERS = {"bioid", "bio", "id", "staffid", "bionumber", "bioidnumber", "biometriccode",
               "biometricid", "biometric", "biocode"}
NAME_HEADERS = {"name", "lecturer", "lecturername", "fullname", "staffname", "names"}
TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "esq", "rev", "sir"}
NAME_FLOOR = 0.6  # below this a written name is "not in roster" (raw text kept)
BIO_CONFLICT_FLOOR = 0.85  # if the written bio ID is someone else, the name must score this
SURE, TIE = 0.75, 0.05  # flag matches below SURE or within TIE of the runner-up


def _key(c) -> str:
    return re.sub(r"[^a-z]", "", str(c).lower())


def _with_header(raw: pd.DataFrame, need) -> pd.DataFrame:
    """Find the header row (skipping title rows) and return the data below it."""
    for i in range(min(len(raw), 20)):
        keys = {_key(v) for v in raw.iloc[i] if pd.notna(v)}
        if all(keys & h for h in need):
            cols = [str(v).strip() if pd.notna(v) else f"col{j}" for j, v in enumerate(raw.iloc[i])]
            return pd.DataFrame(raw.iloc[i + 1:].values, columns=cols)
    raw = raw.copy()
    raw.columns = [str(c) for c in raw.iloc[0]]
    return raw.iloc[1:]


def read_file(uploaded, need=(BIO_HEADERS, NAME_HEADERS)) -> pd.DataFrame:
    """Read every sheet as text, detect each header row, and stack them."""
    if uploaded.name.lower().endswith(".csv"):
        sheets = {"csv": pd.read_csv(uploaded, header=None, dtype=str)}
    else:
        sheets = pd.read_excel(uploaded, sheet_name=None, header=None, dtype=str)
    parts = [_with_header(d, need).assign(sheet=n) for n, d in sheets.items() if len(d)]
    return pd.concat(parts, ignore_index=True)


def guess_columns(df):
    """Return (bio_col, name_col); either may be None if not detected."""
    key = {c: _key(c) for c in df.columns}
    bio = next((c for c in df.columns if key[c] in BIO_HEADERS), None)
    name = next((c for c in df.columns if key[c] in NAME_HEADERS), None)
    return bio, name


def norm_bio(s) -> str:
    s = re.sub(r"\.0+$", "", str(s or "").strip())
    return re.sub(r"[^A-Za-z0-9]", "", s).upper().lstrip("0")


def norm_name(s) -> str:
    words = re.sub(r"[^a-z ]", " ", str(s or "").lower()).split()
    return " ".join(w for w in words if w not in TITLES)


def build(df, bio_col, name_col) -> pd.DataFrame:
    """One row per bio ID: canonical name + all name variants; drops heading/blank rows."""
    r = pd.DataFrame({"bio_id": df[bio_col], "name": df[name_col]}).dropna()
    r["bio_id"] = r.bio_id.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    r["name"] = r.name.astype(str).str.strip()
    r["bio_key"] = r.bio_id.map(norm_bio)
    r = r[(r.bio_key != "") & (r.name != "") & (r.name.map(norm_name) != "")]
    rows = []
    for bk, g in r.groupby("bio_key", sort=False):
        counts = g.name.value_counts()
        canon = max(counts.index, key=lambda n: (counts[n], len(n)))
        variants = sorted({norm_name(n) for n in g.name})
        rows.append({"bio_id": g.bio_id.iloc[0], "name": canon, "bio_key": bk,
                     "variants": variants, "name_key": norm_name(canon), "raw_rows": len(g)})
    out = pd.DataFrame(rows)
    if len(out):
        out["shared_name"] = out.name_key.map(out.name_key.value_counts()) > 1
    return out


def extras_frame(extras) -> pd.DataFrame:
    rows = []
    for e in extras or []:
        nk = norm_name(e["name"])
        rows.append({"bio_id": e.get("bio_id", ""), "name": e["name"], "name_key": nk,
                     "bio_key": norm_bio(e.get("bio_id")) or f"~{nk}", "variants": [nk],
                     "raw_rows": 0})
    return pd.DataFrame(rows)


def combine(base, extras):
    """Excel roster + locally added lecturers (Excel wins on duplicate bio IDs)."""
    parts = [p for p in (base, extras_frame(extras)) if p is not None and len(p)]
    if not parts:
        return None
    r = pd.concat(parts, ignore_index=True).drop_duplicates("bio_key").reset_index(drop=True)
    r["shared_name"] = r.name_key.map(r.name_key.value_counts()) > 1
    return r


def _tok(a: str, b: str) -> float:
    """Token-level score: each token of the shorter name vs its best partner (initials allowed)."""
    ta, tb = a.split(), b.split()
    if len(ta) > len(tb):
        ta, tb = tb, ta
    best = []
    for t in ta:
        if len(t) == 1:
            best.append(0.9 if any(u.startswith(t) for u in tb) else 0.0)
        else:
            best.append(max(SequenceMatcher(None, t, u).ratio() for u in tb))
    return sum(best) / len(ta) * (0.85 + 0.15 * len(ta) / len(tb))


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    plain = SequenceMatcher(None, a, b).ratio()
    swapped = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(plain, swapped, _tok(a, b))


def _ranked(nk, roster):
    sims = roster.variants.map(lambda vs: max(similarity(nk, v) for v in vs))
    return sims.sort_values(ascending=False, kind="stable")


def match_one(name, bio, roster, aliases=None, guess=None):
    """Best roster match for a written name.

    Priority: learned alias > bio ID + name agree > model roster_guess (verified) > fuzzy name.
    Returns dict(name, bio, method, score, alt, note); name == "" means unmatched.
    """
    nk = norm_name(name)
    none = {"name": "", "bio": "", "method": "none", "score": 0.0, "alt": "", "note": ""}
    if aliases and nk in aliases:
        canon = aliases[nk]
        hit = roster.index[roster.name_key == norm_name(canon)] if roster is not None else []
        bio_id = roster.loc[hit[0], "bio_id"] if len(hit) else ""
        canon = roster.loc[hit[0], "name"] if len(hit) else canon
        return {**none, "name": canon, "bio": bio_id, "method": "learned", "score": 1.0}
    if roster is None or not len(roster) or not nk:
        return none
    ranked = _ranked(nk, roster)
    top, top_s = ranked.index[0], float(ranked.iloc[0])
    pick = lambda i, how, sc: {"name": roster.loc[i, "name"], "bio": roster.loc[i, "bio_id"],  # noqa: E731
                               "method": how, "score": round(sc, 2)}
    runner = lambda i: next((roster.loc[j, "name"] for j in ranked.index if j != i), "")  # noqa: E731
    bk = norm_bio(bio)
    hit = list(roster.index[roster.bio_key == bk]) if bk else []
    if hit and float(ranked[hit[0]]) >= 0.6:
        return {**pick(hit[0], "bio+name", max(float(ranked[hit[0]]), SURE)), "alt": runner(hit[0]), "note": ""}
    g = list(roster.index[roster.name_key == norm_name(guess)]) if guess else []
    if g and float(ranked[g[0]]) >= NAME_FLOOR and top_s - float(ranked[g[0]]) < 0.1:
        res, notes = pick(g[0], "model+name", float(ranked[g[0]])), []
    elif top_s >= NAME_FLOOR:
        res, notes = pick(top, "name", top_s), []
    elif hit:
        return {**pick(hit[0], "bio", 0.5), "alt": runner(hit[0]), "note": "roster: matched by bio ID only - check"}
    else:
        return {**none, "score": round(top_s, 2), "alt": roster.loc[top, "name"], "note": "not in roster"}
    i = roster.index[roster.name == res["name"]][0]
    if hit and hit[0] != i and res["score"] < BIO_CONFLICT_FLOOR:
        return {**none, "score": res["score"], "alt": res["name"],
                "note": f"not in roster (bio ID belongs to {roster.loc[hit[0], 'name']}; closest name {res['name']})"}
    alt = runner(i)
    alt_s = float(ranked[roster.index[roster.name == alt][0]]) if alt else 0.0
    if res["score"] < SURE:
        notes.append("low match")
    if alt and res["score"] - alt_s < TIE:
        notes.append("close tie")
    if hit and hit[0] != i:
        notes.append(f"bio ID belongs to {roster.loc[hit[0], 'name']}")
    if roster.loc[i, "shared_name"]:
        notes.append("same name has several bio IDs")
    note = f"roster: {', '.join(notes)} - check (alt: {alt})" if notes else ""
    return {**res, "alt": alt, "note": note}


def _txt(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def apply_roster(df: pd.DataFrame, roster, aliases=None) -> pd.DataFrame:
    """Set lecturer_name to the roster name (unless the clerk typed/picked one) + flags."""
    df = df.copy()
    for c in ("matched_name", "matched_bio_id", "match_method", "alt_name"):
        df[c] = ""
    df["match_score"] = 0.0
    if "name_auto" not in df:
        df["name_auto"] = ""
    if (roster is None or not len(roster)) and not aliases or not len(df):
        return df
    names = set(roster.name) if roster is not None else set()
    for i, row in df.iterrows():
        cur, raw = _txt(row.lecturer_name), _txt(row.get("raw_name"))
        auto = bool(raw) and cur in (raw, _txt(row.get("name_auto")))
        if not auto and cur in names:  # clerk chose a roster name
            j = roster.index[roster.name == cur][0]
            m = {"name": cur, "bio": roster.loc[j, "bio_id"], "method": "clerk", "score": 1.0, "alt": "", "note": ""}
        else:
            m = match_one(raw if auto else cur, row.get("bio_id_hint"), roster, aliases, row.get("roster_guess"))
        if auto and m["name"]:
            df.loc[i, ["lecturer_name", "name_auto"]] = [m["name"], m["name"]]
        elif not auto and m["name"] and m["method"] != "clerk" and not m["note"]:
            m["note"] = f"roster: typed name differs, suggestion {m['name']}"
        df.loc[i, ["matched_name", "matched_bio_id", "match_method", "alt_name"]] = [m["name"], m["bio"], m["method"], m["alt"]]
        df.loc[i, "match_score"] = m["score"]
        if m["note"]:
            df.loc[i, "check"] = "; ".join(x for x in [df.loc[i, "check"], m["note"]] if x)
    return df
