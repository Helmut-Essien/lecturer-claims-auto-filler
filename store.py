"""Small local persistence in data/: app settings and the saved lecturer/course lists."""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
SETTINGS = DATA / "settings.json"


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, data):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def settings() -> dict:
    return _read(SETTINGS, {})


def set_setting(key, value):
    s = settings()
    if s.get(key) != value:
        s[key] = value
        _write(SETTINGS, s)


def load_list(kind):
    """kind = 'roster' | 'courses' -> (DataFrame or None, meta dict)."""
    d = _read(DATA / f"{kind}.json", None)
    if not d or not d.get("rows"):
        return None, {}
    return pd.DataFrame(d["rows"]), d.get("meta", {})


def save_list(kind, df, source):
    """Overwrite the saved list, keeping the previous one as <kind>.prev.json."""
    p = DATA / f"{kind}.json"
    if p.exists():
        shutil.copyfile(p, DATA / f"{kind}.prev.json")
    meta = {"source": source, "updated": datetime.now().isoformat(timespec="minutes"), "count": len(df)}
    _write(p, {"meta": meta, "rows": df.to_dict(orient="records")})
    return meta


def remove_list(kind):
    p = DATA / f"{kind}.json"
    if p.exists():
        shutil.copyfile(p, DATA / f"{kind}.prev.json")
        p.unlink()
