from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_SALES = ROOT / "data_2" / "data" / "sales"
MASTERS_SQL = ROOT / "data_2" / "data" / "masters.sql"
FINANCE_CSV = ROOT / "data_2" / "data" / "finance_monthly.csv"
LAKE = ROOT / "data_lake"
EVIDENCE = ROOT / "evidence"
TRUTH_JSON = RAW_SALES.parent / "_truth" / "truth.json"
MINIO_ENDPOINT = "localhost:9000"
MINIO_BUCKET = "annapurna-sales"

FILE_RE = re.compile(r"^SALES_(S\d+)_(\d{8})(?:__R(\d+))?\.(csv|parquet)$", re.I)
ALIASES = {"item_code": "product_code", "quantity": "qty", "rate": "unit_price", "type": "line_type", "txn_time": "ts"}
CANONICAL_COLUMNS = ["bill_no", "line_no", "product_code", "qty", "unit_price", "line_type", "ts", "_source_file", "_store", "_business_date", "_source_rank"]
RAW_COLUMNS = ["bill_no", "line_no", "product_code", "qty", "unit_price", "line_type", "ts"]


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def parse_filename(path: Path) -> tuple[str, str, int] | None:
    match = FILE_RE.match(path.name)
    if match is None:
        return None
    return match.group(1).upper(), match.group(2), int(match.group(3) or 0)


def sales_files() -> list[Path]:
    return sorted(p for p in RAW_SALES.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".parquet"} and parse_filename(p))


def read_sales_file(path: Path) -> pd.DataFrame:
    metadata = parse_filename(path)
    if metadata is None:
        raise ValueError(f"Unexpected sales filename: {path.name}")
    store, business_date, source_rank = metadata
    if path.suffix.lower() == ".parquet":
        frame = duckdb.connect().execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchdf()
    else:
        first_line = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[0]
        separator = ";" if first_line.count(";") > first_line.count(",") else ","
        frame = pd.read_csv(path, sep=separator, encoding="utf-8-sig", dtype=str)
    frame.columns = [str(column).strip().lstrip("\ufeff") for column in frame.columns]
    frame = frame.rename(columns=ALIASES)
    missing = set(RAW_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns: {sorted(missing)}")
    frame = frame[RAW_COLUMNS].copy()
    frame["bill_no"] = frame["bill_no"].astype("string").str.strip()
    frame["product_code"] = frame["product_code"].astype("string").str.strip()
    frame["line_type"] = frame["line_type"].astype("string").str.strip().str.upper()
    frame["line_no"] = pd.to_numeric(frame["line_no"], errors="coerce")
    frame["qty"] = pd.to_numeric(frame["qty"], errors="coerce")
    frame["unit_price"] = pd.to_numeric(frame["unit_price"], errors="coerce")
    frame["ts"] = frame["ts"].astype("string").str.strip()
    frame["_source_file"] = path.name
    frame["_store"] = store
    frame["_business_date"] = business_date
    frame["_source_rank"] = source_rank
    return frame[CANONICAL_COLUMNS]


def load_raw_sales() -> tuple[pd.DataFrame, dict[str, int]]:
    frames: list[pd.DataFrame] = []
    rejected = 0
    for path in sales_files():
        try:
            frames.append(read_sales_file(path))
        except (ValueError, UnicodeError, pd.errors.ParserError):
            rejected += 1
    if not frames:
        raise RuntimeError("No readable sales files found")
    return pd.concat(frames, ignore_index=True), {"files": len(frames), "rejected_files": rejected}


def canonicalize(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    valid = raw.dropna(subset=["bill_no", "line_no"]).copy()
    valid["line_no"] = valid["line_no"].astype(int)
    key = ["bill_no", "line_no"]
    duplicate_key_rows = int(valid.duplicated(key, keep=False).sum())
    value_columns = ["product_code", "qty", "unit_price", "line_type", "ts"]
    valid["_row_fingerprint"] = valid[value_columns].fillna("<NULL>").astype(str).agg("|".join, axis=1)
    conflicting_keys = int(valid.groupby(key)["_row_fingerprint"].nunique().gt(1).sum())
    valid = valid.sort_values(key + ["_source_rank", "_source_file", "_row_fingerprint"], kind="mergesort")
    canonical = valid.drop_duplicates(key, keep="first").drop(columns="_row_fingerprint")
    canonical = canonical.sort_values(key, kind="mergesort").reset_index(drop=True)
    return canonical[CANONICAL_COLUMNS], {"raw_rows": len(raw), "canonical_rows": len(canonical), "duplicate_key_rows": duplicate_key_rows, "conflicting_keys": conflicting_keys, "invalid_key_rows": len(raw) - len(valid)}


def normalized_timestamp(series: pd.Series) -> pd.Series:
    values = series.astype("string").str.strip()
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.to_datetime(values, errors="coerce", format="mixed")
    epoch_mask = numeric.notna()
    result.loc[epoch_mask] = pd.to_datetime(numeric[epoch_mask], unit="s", errors="coerce", utc=True).dt.tz_localize(None)
    return result


def load_masters() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(MASTERS_SQL.read_text(encoding="utf-8"))
    return con


def ensure_sqlite_masters() -> Path:
    target = LAKE / "masters.sqlite"
    if target.exists():
        return target
    connection = sqlite3.connect(target)
    sqlite_sql = MASTERS_SQL.read_text(encoding="utf-8")
    sqlite_sql = re.sub(r"DROP TABLE IF EXISTS (\w+) CASCADE;", r"DROP TABLE IF EXISTS \1;", sqlite_sql)
    sqlite_sql = re.sub(r"DATE '([^']+)'", r"'\1'", sqlite_sql)
    connection.executescript(sqlite_sql)
    connection.commit()
    connection.close()
    return target


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_evidence(name: str, text: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(text.rstrip() + "\n", encoding="utf-8")