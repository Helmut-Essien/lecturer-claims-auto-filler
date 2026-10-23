# Lecturer Claims Auto-Filler

Photograph handwritten lecturer attendance register pages, extract the fields with
**Gemma 4** (via the Gemini API), review/correct them in an editable table, and save to
the Microsoft Access claims database (CSV fallback if Access is unavailable).

Saved fields: attendance_date (the date YOU select), course_code, lecturer_name,
time_in, time_out (HH:MM 24h), duration_minutes. Bio ID is shown only as a read-only
hint; signatures are ignored.

## Setup & run
```bash
cd "/home/helmut/Documents/My Projects/lecturer-claims-auto-filler"
# optional: python3 -m venv .venv && source .venv/bin/activate   (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
cp .env.example .env        # then edit .env and paste your key
streamlit run app.py
```
Use whatever Python 3.10+ environment you already have active, or create a venv yourself.

## Lecturer roster (optional)
Upload an `.xlsx` or `.csv` in the sidebar with one row per lecturer and two columns:
a bio ID column (header like `Bio ID`, `BioID`, `ID`, `Staff ID`) and a name column
(`Name`, `Lecturer`, `Lecturer Name`, `Full Name`). Headers are detected case-insensitively;
pick them from the dropdowns if not. The roster stays loaded across batches.

Each row is matched by bio ID (leading zeros/punctuation ignored) and by fuzzy name
(titles like Dr/Prof/Esq ignored). Confident matches replace the extracted name with the
roster spelling; weak or conflicting matches are flagged `roster: check name`, and unknown
lecturers `not in roster`. Matched bio IDs are display-only and are not saved to Access.
Rows longer than 4 hours are flagged `unusually long class`.

## Course list (optional)
Upload an `.xlsx`/`.csv` in the sidebar with a course code column (`Course Code`, `Code`,
`Course`, ...) and optionally a title column; multiple sheets and title rows are handled.
Codes are normalized (`mps414` = `MPS 414`); suffixes like `EVE` are dropped and cells with
two codes use the best one and are flagged.

## Official spellings
With a roster / course list loaded, every row's lecturer name and course code are set to the
**official list spelling** (best match: learned alias > bio ID + name > model's roster guess >
fuzzy match on all name variants, titles/initials/word order ignored). The handwriting stays in
*Written as* / *Course written as*. Weak matches, near-ties and bio-ID conflicts are flagged with
the runner-up: tick **↔ alt** or use the **pick** dropdown. Names scoring below 0.6 (courses: 0.5) stay unmatched, and so do names
scoring below 0.85 when the written bio ID belongs to a different lecturer
(`not in roster` / `course not in list`). A value you type or pick yourself is never overwritten.

## Using the app (4 steps)
1. **Date & roster** – pick the attendance date; optionally load the lecturers list in the sidebar.
2. **Upload** – drop all register photos for that day; thumbnails appear; click **Extract**
   (live progress per page; transient 500/503 API errors are retried twice).
3. **Review** – metrics row, date-mismatch banner, editable table (flagged rows first, or
   "Show only flagged"). Tick **accept** to take the roster suggestion or choose from
   **pick from roster**. Minutes recompute immediately. Lecturers missing from the Excel list
   can be added to a local supplement (`learning/extra_lecturers.json`).
4. **Save** – per-lecturer summary, confirm date if mismatched, save to Access/CSV, download CSV.

## How "learning" works (honest version)
Nothing is fine-tuned and no images are stored. On every save, rows the clerk changed
(name, course, times differ from what the model read) are appended to
`learning/corrections.json`. That memory is reused in two ways:
- **Aliases** – a name misread the same way again is matched instantly (`match_method = learned`).
- **Prompt hints** – up to 40 frequent/recent corrections (e.g. `name read as "Awudu Atunji"
  is actually "Awudu Atingi"`) plus the roster names are added to the extraction prompt, and
  the model returns a `roster_guess` per row, which Python double-checks.
Learned course fixes are applied automatically to new extractions. The sidebar shows
counts and lets you reset the memory. Everything in `learning/` is local and gitignored.
Note: approving a row also teaches any automatic roster replacement it contains, so check
flagged rows before saving.

## Getting a Gemini API key
1. Go to https://aistudio.google.com/apikey and sign in with a Google account.
2. Click **Create API key**, copy it.
3. Put it in `.env` as `GEMINI_API_KEY=...`. Optionally set `GEMMA_MODEL` (default `gemma-4-31b-it`).

## Access / Windows note
Saving to Access needs Windows plus the **"Microsoft Access Driver (*.mdb, *.accdb)"**
ODBC driver (install the *Microsoft Access Database Engine Redistributable*, matching
your Python's 32/64-bit). Set `ACCESS_DB_PATH` in `.env` to your `.accdb` file. The
`LecturerClaims` table is created if missing.

If the driver, the path, or the connection is unavailable (e.g. on Linux/macOS), the app
automatically writes `./exports/lecturer_claims_<date>_<timestamp>.csv` and tells you
which destination was used. A "Download table as CSV" button is always available.

## Assumptions
- One batch = one date; the date you pick is authoritative. The handwritten sheet date is
  only evidence: any mismatch (or unreadable date) blocks saving until you confirm.
- English handwriting; times are same-day (time_out after time_in).
- The clerk does not need bio_id or signatures saved.
- One project folder = one deploy.

## 60-second smoke test
1. Pick the date, upload 2 register photos.
2. Click **Extract** → one combined table with `source_file` per row.
3. If a date mismatch banner appears, tick "I confirm the batch date is correct".
4. Edit one row's time_out → duration updates; add/delete a row if needed.
5. Click **Approve & Save** → see Access or `exports/*.csv` destination reported.

## Use a local model (Ollama)

Runs fully offline, no API key. Caveat: the model **must include its vision projector (mmproj)**.
A plain text GGUF (e.g. `FROM ./gemma-4-E4B-it-Q4_K_M.gguf` alone) shows only
`completion/tools/thinking` in `ollama show` and cannot read images.

Commands that worked (RTX 2050, 4 GB):

```bash
cd ~/models
wget -c https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf
printf 'FROM ./gemma-4-E4B-it-Q4_K_M.gguf\nFROM ./mmproj-F16.gguf\n' > Modelfile.vision
ollama create gemma4-e4b-vision -f Modelfile.vision
ollama show gemma4-e4b-vision   # Capabilities must list "vision"
```

(Alternative: `ollama pull gemma4:e4b`, ~6.5 GB, includes the projector.)

Then in `.env`: `LLM_BACKEND=ollama`, `OLLAMA_MODEL=gemma4-e4b-vision` (or pick it in
sidebar → Settings). One page takes ~2 min locally. Accuracy is much lower than
`gemma-4-31b-it` (names misread/repeated), so review every row before approving.

## Saved settings and lists (data/)

- **AI reader**: choose *Online (Google Gemini)* or *Local (Ollama)* at the top of the sidebar.
  The choice is remembered in `data/settings.json` (first run: `LLM_BACKEND`, else Online).
  Online needs `GEMINI_API_KEY`; Local shows whether Ollama is running and the model is installed.
  Model names are under *Advanced*. "Send lecturer list + hints" defaults ON for Online, OFF for Local.
- **Lecturer list / Course list** are saved to `data/roster.json` / `data/courses.json` and load
  automatically on start. Use *Replace lecturer list* → upload → check count/columns →
  **Replace saved list** (the previous one is kept as `data/roster.prev.json`).
  *Remove saved list* needs a confirm tick. Lecturers added while reviewing still merge in.
- `data/*.json` is gitignored (local data).
