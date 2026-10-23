"""Save approved rows to MS Access via pyodbc, falling back to CSV."""
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

EXPORT_DIR = Path(__file__).resolve().parent / "exports"
DRIVER = "Microsoft Access Driver (*.mdb, *.accdb)"
CREATE_SQL = ("CREATE TABLE LecturerClaims (LecturerName TEXT(255), CourseCode TEXT(50), "
              "AttendanceDate DATE, TimeIn TEXT(5), TimeOut TEXT(5), "
              "DurationMinutes LONG, SourceFile TEXT(255))")
INSERT_SQL = ("INSERT INTO LecturerClaims (LecturerName, CourseCode, AttendanceDate, "
              "TimeIn, TimeOut, DurationMinutes, SourceFile) VALUES (?, ?, ?, ?, ?, ?, ?)")


def _records(df, attendance_date):
    return [(r.lecturer_name, r.course_code, attendance_date, r.time_in, r.time_out,
             int(r.duration_minutes) if pd.notna(r.duration_minutes) else None,
             r.source_file) for r in df.itertuples()]


def _save_csv(df, attendance_date) -> str:
    EXPORT_DIR.mkdir(exist_ok=True)
    path = EXPORT_DIR / f"lecturer_claims_{attendance_date}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    out = df[["lecturer_name", "course_code", "time_in", "time_out",
              "duration_minutes", "source_file"]].copy()
    out.insert(2, "attendance_date", str(attendance_date))
    out.to_csv(path, index=False)
    return str(path)


def save_to_access(df: pd.DataFrame, attendance_date) -> tuple[str, str, str]:
    """Returns (method, destination, error_or_empty)."""
    db_path = os.getenv("ACCESS_DB_PATH", "")
    try:
        import pyodbc
        if not db_path:
            raise RuntimeError("ACCESS_DB_PATH not set")
        if DRIVER not in pyodbc.drivers():
            raise RuntimeError(f"ODBC driver '{DRIVER}' not installed")
        conn = pyodbc.connect(f"DRIVER={{{DRIVER}}};DBQ={db_path};")
        cur = conn.cursor()
        if not cur.tables(table="LecturerClaims", tableType="TABLE").fetchone():
            cur.execute(CREATE_SQL)
        cur.executemany(INSERT_SQL, _records(df, attendance_date))
        conn.commit()
        conn.close()
        return "access", db_path, ""
    except Exception as e:  # driver missing, bad path, non-Windows, etc.
        return "csv", _save_csv(df, attendance_date), str(e)
