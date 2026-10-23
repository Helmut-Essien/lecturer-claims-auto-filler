"""Lecturer Claims Auto-Filler - Streamlit UI."""
import io
import json
import os
import urllib.request
import re
import time
from datetime import date, datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from PIL import Image

load_dotenv()
import learning  # noqa: E402
import courses as cs  # noqa: E402
import roster as rs  # noqa: E402
import store  # noqa: E402
from db import save_to_access  # noqa: E402
import extractor as ex  # noqa: E402
from extractor import ExtractionError, build_prompt, extract_page  # noqa: E402

st.set_page_config(page_title="Lecturer Claims Auto-Filler", page_icon="📋", layout="wide")

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULTS = {"lecturer_name": "", "course_code": "", "time_in": "", "time_out": "",
            "confidence": 1.0, "source_file": "manual", "bio_id_hint": "", "notes": "",
            "raw_name": "", "raw_course": "", "raw_time_in": "", "raw_time_out": "",
            "roster_guess": "", "name_auto": "", "course_auto": ""}
SHOWN = ["flag", "lecturer_name", "roster_pick", "use_alt", "alt_name", "course_code", "course_pick",
         "use_alt_course", "alt_course", "time_in", "time_out", "duration_minutes", "confidence",
         "raw_name", "raw_course", "match_method", "match_score", "course_method", "course_score",
         "check", "notes", "source_file", "bio_id_hint"]
ROSTER_COLS = {"roster_pick", "use_alt", "alt_name", "match_method", "match_score"}
COURSE_COLS = {"course_pick", "use_alt_course", "alt_course", "course_method", "course_score"}
EDITABLE = {"lecturer_name", "roster_pick", "use_alt", "course_code", "course_pick",
            "use_alt_course", "time_in", "time_out"}
PICKS = {"use_alt": ("lecturer_name", "alt_name"), "roster_pick": ("lecturer_name", None),
         "use_alt_course": ("course_code", "alt_course"), "course_pick": ("course_code", None)}

ss = st.session_state
for k, v in {"df": None, "mismatches": [], "log": [], "batch_date": None, "saved": None,
             "roster": None, "courses": None, "ed_v": 0, "view_index": []}.items():
    ss.setdefault(k, v)


def duration(t_in, t_out):
    if not (TIME_RE.match(str(t_in or "")) and TIME_RE.match(str(t_out or ""))):
        return None
    mins = int((datetime.strptime(t_out, "%H:%M") - datetime.strptime(t_in, "%H:%M")).total_seconds() // 60)
    return mins if mins > 0 else None


def roster_full():
    return rs.combine(ss.roster, learning.extras())


def checks(df):
    """Recompute durations, roster/learned matches and flags (idempotent)."""
    df = df.copy().reset_index(drop=True)
    for c, v in DEFAULTS.items():
        df[c] = df[c].fillna(v) if c in df else v
    for c in ("lecturer_name", "course_code", "time_in", "time_out"):
        df[c] = df[c].astype(str).str.strip()
    df["course_code"] = df.course_code.str.upper()
    df["confidence"] = pd.to_numeric(df.confidence, errors="coerce").fillna(0.0)
    df["duration_minutes"] = [duration(a, b) for a, b in zip(df.time_in, df.time_out)]
    df["check"] = ["invalid time (HH:MM, out after in)" if d is None else
                   "unusually long class" if d > 240 else "" for d in df.duration_minutes]
    df = rs.apply_roster(df, roster_full(), learning.aliases())
    df = cs.apply_courses(df, ss.courses, learning.course_fixes())
    df["use_alt"], df["use_alt_course"], df["roster_pick"], df["course_pick"] = False, False, None, None
    df["flag"] = (df.confidence < 0.7) | (df.notes != "") | (df.check != "")
    return df


def on_edit():
    """Apply table edits to the working DataFrame, recompute, and refresh the editor."""
    delta, df, vi = ss[f"editor_{ss.ed_v}"], ss.df.copy(), ss.view_index
    for pos, changes in delta["edited_rows"].items():
        idx = vi[int(pos)]
        for col, val in changes.items():
            if col in PICKS:
                target, src = PICKS[col]
                new = df.at[idx, src] if src else val
                if val and new:
                    df.at[idx, target] = new
            else:
                df.at[idx, col] = val
    df = df.drop(index=[vi[p] for p in delta["deleted_rows"]])
    for row in delta["added_rows"]:
        new = {**DEFAULTS, **{k: v for k, v in row.items() if v not in (None, "")}, "notes": "manual row"}
        df = pd.concat([df, pd.DataFrame([new])], ignore_index=True)
    ss.df = checks(df)
    ss.ed_v += 1


def parse_sheet_date(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


# ---------------- Sidebar: AI, lists, learning ----------------
BACKENDS = {"gemini": "Online (Google Gemini)", "ollama": "Local (Ollama)"}


@st.cache_data(ttl=30, show_spinner=False)
def ollama_status(host, model):
    """(ok, message) from a quick /api/tags ping; never blocks more than ~2 s."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return False, f"Ollama not reachable at {host}"
    if model in names or f"{model}:latest" in names:
        return True, f"Ollama running · model `{model}` available"
    return False, f"Ollama running, but model `{model}` is not installed"


def list_editor(kind, label, reader, guesser, builder, need_two):
    """Saved-list summary + 'Replace' expander + 'Remove' for roster/courses."""
    df, meta = store.load_list(kind)
    if df is not None:
        st.success(f"{label}: **{len(df)}** {'lecturers' if kind == 'roster' else 'codes'}")
        st.caption(f"Last updated {meta.get('updated', '?').replace('T', ' ')} · from `{meta.get('source', '?')}`")
    else:
        st.caption(f"No saved {label.lower()} yet. Add one below.")
    with st.expander(f"Replace {label.lower()}" if df is not None else f"Add {label.lower()}"):
        up = st.file_uploader("Excel/CSV", type=["xlsx", "xls", "csv"], key=f"up_{kind}_{ss.get('up_v', 0)}")
        if up is not None:
            try:
                raw = reader(up)
                g1, g2 = guesser(raw)
                cols = list(raw.columns)
                if need_two:
                    c1 = st.selectbox("Bio ID column", cols, index=cols.index(g1) if g1 else 0, key=f"c1_{kind}")
                    c2 = st.selectbox("Name column", cols, index=cols.index(g2) if g2 else 0, key=f"c2_{kind}")
                else:
                    c1 = st.selectbox("Code column", cols, index=cols.index(g1) if g1 else 0, key=f"c1_{kind}")
                    c2 = st.selectbox("Title column", [None] + cols,
                                      index=cols.index(g2) + 1 if g2 else 0, key=f"c2_{kind}")
                new = builder(raw, c1, c2)
                st.info(f"`{up.name}`: **{len(new)}** entries found"
                        + (f" (was {len(df)})" if df is not None else ""))
                if st.button("Replace saved list" if df is not None else "Save list",
                             type="primary", disabled=not len(new), key=f"save_{kind}"):
                    store.save_list(kind, new, up.name)
                    ss.up_v = ss.get("up_v", 0) + 1
                    st.rerun()
            except Exception as e:
                st.error(f"Could not read file: {e}")
        if df is not None:
            sure = st.checkbox("Yes, remove the saved list", key=f"rmok_{kind}")
            if st.button("Remove saved list", disabled=not sure, key=f"rm_{kind}"):
                store.remove_list(kind)
                st.rerun()
    return df


with st.sidebar:
    st.header("🤖 AI reader")
    saved_be = store.settings().get("backend") or (ex.BACKEND if ex.BACKEND in BACKENDS else "gemini")
    backend = st.segmented_control("AI reader", list(BACKENDS), format_func=BACKENDS.get,
                                   default=saved_be, key="backend", label_visibility="collapsed")
    backend = backend or saved_be
    store.set_setting("backend", backend)
    with st.expander("Advanced"):
        model = st.text_input("Model", value=ex.MODEL if backend == "gemini" else ex.OLLAMA_MODEL,
                              key=f"model_{backend}")
        if backend == "ollama":
            st.caption(f"Host: {ex.OLLAMA_HOST}")
    if backend == "gemini":
        (st.success if os.getenv("GEMINI_API_KEY") else st.error)(
            "API key found" if os.getenv("GEMINI_API_KEY") else "GEMINI_API_KEY missing in .env")
    else:
        ok, msg = ollama_status(ex.OLLAMA_HOST, model)
        (st.success if ok else st.error)(msg)
        st.caption("⚠️ Local reading is less accurate (about half the times right in tests). "
                   "Check every row before saving.")
    use_hints = st.toggle("Send lecturer list + learned hints to the AI",
                          value=backend == "gemini", key=f"hints_{backend}")

    st.header("👥 Lecturer list")
    ss.roster = list_editor("roster", "Lecturer list", rs.read_file, rs.guess_columns, rs.build, True)
    full = roster_full()
    n_x = len(learning.extras())
    if n_x:
        st.caption(f"+ {n_x} lecturer(s) added locally while reviewing")

    st.header("📚 Course list")
    ss.courses = list_editor("courses", "Course list", cs.read_file, cs.guess_columns, cs.build, False)

    st.header("🧠 Learning")
    stt = learning.stats()
    c1, c2 = st.columns(2)
    c1.metric("Corrections", stt["corrections"])
    c2.metric("Name aliases", stt["aliases"])
    with st.expander("Details"):
        for name, n in stt["top"]:
            st.write(f"• {name}: {n} correction(s)")
        hints = learning.hints()
        if hints:
            st.caption("Hints sent to the model:")
            st.code("\n".join(hints[:10]), language=None)
        sure = st.checkbox("Yes, erase all learned corrections and added lecturers")
        if st.button("Reset learning memory", disabled=not sure):
            learning.reset()
            st.rerun()

if backend == "gemini" and not os.getenv("GEMINI_API_KEY"):
    st.error("GEMINI_API_KEY is missing. Add it to .env, or choose **Local (Ollama)** in the sidebar.")
    st.stop()

# ---------------- Main ----------------
st.title("📋 Lecturer Claims Auto-Filler")
st.caption("Photograph the register → AI reads it → you review → save to Access")
locked = ss.df is not None

with st.container(border=True):
    st.subheader("① Date & roster")
    c1, c2 = st.columns([1, 2])
    batch_date = c1.date_input("Attendance date (saved on every row)",
                               value=ss.batch_date or date.today(), disabled=locked)
    c2.info(f"Roster: **{len(full)} lecturers** loaded." if full is not None
            else "No roster loaded — names will be saved as read. Upload one in the sidebar.")

with st.container(border=True):
    st.subheader("② Upload register pages")
    files = st.file_uploader("Photos of the register (several pages for the same day)",
                             type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
                             disabled=locked, label_visibility="collapsed")
    if files and not locked:
        grid = st.columns(6)
        for i, f in enumerate(files):
            with grid[i % 6]:
                try:
                    st.image(Image.open(io.BytesIO(f.getvalue())), caption=f.name, width=150)
                except Exception:
                    st.warning(f"{f.name}: not a readable image")
    if st.button("🔍 Extract", type="primary", disabled=locked or not files):
        names = [f"{n} ({b})" for n, b in zip(full.name, full.bio_id)] if full is not None and use_hints else None
        codes = list(ss.courses.code) if ss.courses is not None and use_hints else None
        prompt = build_prompt(names, learning.hints() if use_hints else None, codes)
        rows, mism, log = [], [], []
        with st.status(f"Reading {len(files)} page(s)…", expanded=True) as status:
            for f in files:
                t0 = time.time()
                st.write(f"⏳ {f.name}…")
                try:
                    res = extract_page(f.getvalue(), f.name, prompt=prompt, backend=backend, model=model)
                    rows += res["entries"]
                    sd = parse_sheet_date(res["sheet_date"])
                    if sd != batch_date:
                        mism.append(f"**{f.name}**: sheet says {sd or 'unreadable'}, batch says {batch_date}")
                    log.append(f"✅ {f.name}: {len(res['entries'])} rows · sheet date {sd} · {time.time()-t0:.0f}s")
                except ExtractionError as e:
                    log.append(f"❌ {f.name}: {e} · {time.time()-t0:.0f}s")
                st.write(log[-1])
            status.update(label="Extraction finished", state="error" if not rows else "complete")
        ss.df = checks(pd.DataFrame(rows)) if rows else None
        ss.mismatches, ss.log, ss.batch_date, ss.saved = mism, log, batch_date, None
        ss.ed_v += 1
        st.rerun()
    if ss.log:
        with st.expander("Extraction log", expanded=any(m.startswith("❌") for m in ss.log)):
            for m in ss.log:
                (st.warning if m.startswith("❌") else st.write)(m)

if ss.df is not None and "flag" not in ss.df:
    ss.df = checks(ss.df)
if ss.df is not None:
    df, bd = ss.df, ss.batch_date
    with st.container(border=True):
        st.subheader(f"③ Review — 🔒 {bd:%A %d %B %Y}")
        m = st.columns(5)
        m[0].metric("Entries", len(df))
        m[1].metric("Images", df.source_file[df.source_file != "manual"].nunique())
        m[2].metric("Hours", f"{df.duration_minutes.fillna(0).sum() / 60:.1f}")
        m[3].metric("Flagged", int(df.flag.sum()))
        m[4].metric("Matched to roster", f"{(df.match_method != 'none').mean():.0%}" if full is not None else "—")
        if ss.mismatches:
            st.error("🚨 **DATE MISMATCH** — the handwritten date differs from the batch date:\n\n"
                     + "\n".join(f"- {x}" for x in ss.mismatches)
                     + "\n\nYou must confirm the batch date in step ④ before saving.")
        st.caption("Names and course codes are replaced by the official list spelling (original in "
                   "*Written as*). Flagged rows come first: tick **↔ alt** for the runner-up or use "
                   "the **pick** dropdowns. Durations update as you edit.")
        only = st.toggle("Show only flagged rows")
        view = df.sort_values("flag", ascending=False, kind="stable")
        view = view[view.flag] if only else view
        ss.view_index = list(view.index)
        view = view.reset_index(drop=True)
        order = [c for c in SHOWN if (full is not None or c not in ROSTER_COLS)
                 and (ss.courses is not None or c not in COURSE_COLS)]
        cc = st.column_config
        st.data_editor(
            view, key=f"editor_{ss.ed_v}", on_change=on_edit, hide_index=True,
            num_rows="dynamic", width="stretch", column_order=order,
            disabled=[c for c in view.columns if c not in EDITABLE],
            column_config={
                "flag": cc.CheckboxColumn("⚠️", width="small"),
                "lecturer_name": cc.TextColumn("Lecturer", width="medium", required=True),
                "roster_pick": cc.SelectboxColumn("pick lecturer", options=sorted(full.name) if full is not None else []),
                "use_alt": cc.CheckboxColumn("↔ alt", help="Switch to the runner-up roster name"),
                "alt_name": cc.TextColumn("Runner-up"),
                "course_code": cc.TextColumn("Course"),
                "course_pick": cc.SelectboxColumn("pick course", options=sorted(ss.courses.code) if ss.courses is not None else []),
                "use_alt_course": cc.CheckboxColumn("↔ alt", help="Switch to the runner-up course"),
                "alt_course": cc.TextColumn("Course runner-up"),
                "raw_course": cc.TextColumn("Course written as"),
                "course_method": cc.TextColumn("Course match"),
                "course_score": cc.NumberColumn("C. score", format="%.2f"),
                "time_in": cc.TextColumn("In", help="HH:MM 24h"),
                "time_out": cc.TextColumn("Out", help="HH:MM 24h"),
                "duration_minutes": cc.NumberColumn("Minutes", format="%d"),
                "confidence": cc.ProgressColumn("AI conf.", min_value=0, max_value=1, format="%.2f"),
                "raw_name": cc.TextColumn("Written as"),
                "match_method": cc.TextColumn("Match"),
                "match_score": cc.NumberColumn("Score", format="%.2f"),
                "check": cc.TextColumn("Check"), "notes": cc.TextColumn("AI notes"),
                "source_file": cc.TextColumn("Page"), "bio_id_hint": cc.TextColumn("Bio (read)"),
            })
        missing = sorted(df.lecturer_name[df.match_method == "none"].unique()) if full is not None else []
        if missing:
            with st.expander(f"➕ {len(missing)} lecturer(s) not in roster — add to local supplement"):
                with st.form("add_extra", clear_on_submit=True):
                    pick = st.selectbox("Name as read", missing)
                    fixed = st.text_input("Correct full name (blank = as read)")
                    bio = st.text_input("Bio ID (optional)")
                    if st.form_submit_button("Add to roster"):
                        learning.add_extra(fixed or pick, bio)
                        ss.df = checks(ss.df)
                        ss.ed_v += 1
                        st.rerun()

    with st.container(border=True):
        st.subheader("④ Save")
        bad = df[df.duration_minutes.isna()]
        summary = df.groupby("lecturer_name").agg(sessions=("course_code", "count"),
                                                  courses=("course_code", lambda s: ", ".join(sorted(set(s)))),
                                                  hours=("duration_minutes", lambda s: round(s.fillna(0).sum() / 60, 2)))
        st.dataframe(summary, width="stretch")
        confirmed = True
        if ss.mismatches:
            confirmed = st.checkbox(f"I confirm the batch date is correct (**{bd}**)")
        if len(bad):
            st.error(f"{len(bad)} row(s) have invalid times — fix them before saving.")
        if int(df.flag.sum()):
            st.warning(f"⚠️ {int(df.flag.sum())} rows are flagged. Saving is allowed, but please check them.")
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ Approve & Save to Access", type="primary",
                     disabled=bool(ss.saved) or not (confirmed and len(df) and not len(bad))):
            method, dest, err = save_to_access(df, attendance_date=bd)
            ss.saved = (method, dest, err, len(df), learning.record(df, bd))
        out = df[["lecturer_name", "course_code", "time_in", "time_out", "duration_minutes",
                  "source_file"]].assign(attendance_date=str(bd))
        c2.download_button("⬇️ Download CSV", out.to_csv(index=False),
                           file_name=f"lecturer_claims_{bd}.csv", mime="text/csv")
        if ss.saved:
            method, dest, err, n, learned = ss.saved
            where = "Access" if method == "access" else "CSV (Access unavailable)"
            st.success(f"Saved {n} rows to {where}: `{dest}`" + (f" · 🧠 learned {learned} correction(s)" if learned else ""))
            if err:
                st.caption(f"Access error: {err}")

if st.button("🔄 Start new batch"):
    keep = (ss.roster, ss.courses)
    ss.clear()
    ss.roster, ss.courses = keep
    st.rerun()
