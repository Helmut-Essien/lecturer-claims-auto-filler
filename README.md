# Lecturer Claims Auto-Filler

A Streamlit app that turns photos of handwritten lecturer attendance registers into
lecturer-claim rows. An AI vision model reads each page, you review and correct the
results, and approved rows are saved to the Microsoft Access claims database (or to CSV
if Access isn't available).

## The problem it solves
Clerks currently re-type every register row (course code, lecturer name, time in/out) by
hand, fix misspelled names, and work out durations themselves. This app does the reading
and the checking. The clerk confirms or corrects the results, and each correction teaches
the app for next time.

## Features
- **Multi-image extraction:** upload several register pages for the same day and get one
  combined table, with the source file recorded on every row.
- **The batch date wins:** the date you pick is saved on every row. If the date written
  on a page differs (or can't be read), a **DATE MISMATCH** banner appears, and saving is
  blocked until you confirm the batch date.
- **Review table:** editable cells, flagged rows sorted first, a "show only flagged" view,
  live duration (minutes) and validity checks (HH:MM format, time out after time in, more
  than 4 h flagged), "↔ alt" runner-up and "pick" dropdowns, and added or deleted rows.
- **Saved lecturer and course lists:** stored in `data/` and loaded on every start.
  *Replace list* → upload Excel/CSV (all sheets are read and header rows found
  automatically) → check the column mapping and count → **Replace saved list**. The
  previous list is kept as `*.prev.json`. *Remove saved list* needs a confirm tick.
- **The official list decides names and codes:** names and codes are replaced by the
  official spelling, and the original is kept in *Written as*.
  - **Names:** matched in this order: a learned alias, then bio ID + name, then the
    model's roster guess, then fuzzy matching (titles and initials ignored).
    `NAME_FLOOR = 0.6`: below this, the row is marked **not in roster** and the raw text
    is kept. `BIO_CONFLICT_FLOOR = 0.85`: if the written bio ID belongs to someone else,
    the name must score at least this, otherwise the row is flagged.
  - **Course codes:** normalised (`mps414` → `MPS 414`) and matched to the course list.
    `COURSE_FLOOR = 0.5`: below this, the code is marked "not in list". Suffixes such as
    `EVE` and two-code cells are handled.
- **`NOT FOUND` default:** when a list is loaded and a name or code doesn't match anything
  in it, the cell is set to `NOT FOUND` (constant `NOT_FOUND` in `roster.py`).
  - **Names:** this happens below `NAME_FLOOR`, under the `BIO_CONFLICT_FLOOR` rule, or
    when only the bio ID matches and the name doesn't.
  - **Codes:** this happens below `COURSE_FLOOR`.
  - **What's kept:** the raw text stays in *Written as* / *Course written as*, the closest
    candidate is offered as the runner-up (↔ alt), and the row is flagged
    `not in roster` / `not in course list`.
  - **Clerk:** can type a value, pick one (the dropdowns also offer `NOT FOUND`), or add the
    lecturer via ➕, which replaces `NOT FOUND`.
  - **Saving:** `NOT FOUND` rows can be saved. The Review step shows a NOT FOUND count and
    the Save step shows a warning.
  - **Learning:** `NOT FOUND` is never learned as an alias or fix.
  - With no list loaded, the raw value is used as before.
- **Learning memory:** on save, the clerk's corrections become name aliases, course
  fixes and handwriting hints sent to the model next time. Lecturers added during review
  are added to the list. The sidebar shows statistics and has a reset (needs a confirm tick).
- **Online or Local AI:** a switch at the top of the sidebar, remembered in
  `data/settings.json`.
  - *Online (Google Gemini):* uses `gemma-4-31b-it` and shows whether the API key is present.
  - *Local (Ollama):* pings Ollama and shows whether the model is installed.
  - "Send list + hints to the AI" defaults ON for Online and OFF for Local. Model names
    are under *Advanced*.

## Setup
Requires Python 3.10+ (tested with 3.12).
```bash
cd "lecturer-claims-auto-filler"
python3 -m venv .venv            # Ubuntu: sudo apt install python3.12-venv
# If ensurepip is missing and you can't install it:
#   python3 -m venv --without-pip .venv && curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then edit .env
streamlit run app.py
```

### `.env`
| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Required only for Online |
| `GEMMA_MODEL` | Online model (default `gemma-4-31b-it`) |
| `LLM_BACKEND` | `gemini` or `ollama`, used on first run only (afterwards the sidebar choice is remembered) |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Default `http://localhost:11434` / `gemma4-e4b-vision` |
| `ACCESS_DB_PATH` | Path to the `.accdb`/`.mdb` file (optional) |

### Getting a Gemini API key
1. Go to <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and copy it.
3. Paste it into `.env` as `GEMINI_API_KEY=...`. Never commit `.env`.

### Local model (Ollama)
The model **must include its vision projector (mmproj)**. A plain text GGUF only lists
`completion/tools/thinking` under `ollama show` and can't read images. These are the
commands that worked (RTX 2050, 4 GB):
```bash
cd ~/models
wget -c https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf
printf 'FROM ./gemma-4-E4B-it-Q4_K_M.gguf\nFROM ./mmproj-F16.gguf\n' > Modelfile.vision
ollama create gemma4-e4b-vision -f Modelfile.vision
ollama show gemma4-e4b-vision     # Capabilities must list "vision"
```
Alternative: `ollama pull gemma4:e4b` (about 6.5 GB, projector included). After
`ollama create`, the GGUF files can be deleted because Ollama keeps its own copy.

### Microsoft Access
Saving to Access uses `pyodbc` with the **Microsoft Access Database Engine** ODBC driver,
which is Windows only (its bitness must match Python's). Set `ACCESS_DB_PATH`. The
`LecturerClaims` table is created if it's missing. If Access isn't available (Linux/macOS,
no driver, no path), rows are written to `exports/lecturer_claims_<date>_<timestamp>.csv`
instead, and the app tells you which destination was used.

## File structure
```
app.py           Streamlit UI (sidebar: AI reader, lists, learning; steps ①–④)
extractor.py     Prompt + Gemini / Ollama calls, JSON parsing, retries
roster.py        Lecturer list reading/normalising + name matching
courses.py       Course list reading + code matching
learning.py      Learning memory (corrections, aliases, hints, added lecturers)
store.py         data/ persistence (settings, saved lists)
db.py            Access save with CSV fallback
data/            settings.json, roster.json (+ .prev), courses.json (+ .prev)   [gitignored]
learning/        corrections.json, extra_lecturers.json                         [gitignored]
exports/         CSV fallback output                                            [gitignored]
```

## Assumptions
- All pages in a batch are from the same day. The batch date is the date that's saved.
- Times are on the same day, 07:00–22:00. The model converts am/pm to 24 h, and a class
  longer than 4 h is flagged.
- Bio IDs are shown as hints only, and signatures are ignored. The saved fields are
  date, course code, lecturer name, time in/out, duration and source file.

## Known limitations
- **Local accuracy (gemma4-e4b-vision, test page with 13 rows):** about 100 s per page.
  Date and row count were right, but only 6–7 of 13 time pairs, about 10/13 codes and
  11–12/13 names after matching. Check every row.
- **Online (gemma-4-31b-it):** much more accurate (11/13 time pairs, all codes right)
  but slow, about 160 s per page. It sometimes returns 503/500 errors, which are retried
  automatically (3 attempts).
- Handwriting with no am/pm marker can still be misread, so review flagged rows.
- The Access driver is Windows only.

## Smoke test
1. `streamlit run app.py`, then check that the sidebar shows the AI status and "Lecturer list: N lecturers".
2. Pick the date and upload 2 register photos.
3. Click **Extract** to get one combined table with `source_file` on every row.
4. Edit one row's time_out and check that the duration updates. If the date banner appears, tick to confirm.
5. Click **Approve & Save** and check that the Access or `exports/*.csv` destination is reported.
