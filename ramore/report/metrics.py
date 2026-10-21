"""The box's metrics log (`box/sample.py`): one row per scope and sample, archived by month."""

import datetime as dt
import json
from typing import Iterable

import polars as pl
from shackleton import TableRepo

from .. import alert, config
from .parse import epoch_to_datetime

SCHEMA = {
    "t": pl.Float64,
    "scope": pl.String,
    "name": pl.String,
    "used": pl.Int64,
    "limit": pl.Int64,
    "anon": pl.Int64,
    "fds": pl.Int64,
    "fd_limit": pl.Int64,
    "procs": pl.Int64,
}

GB = 2**30

_YEAR_MONTH = "year_month"


def parse_lines(lines: Iterable[str]) -> tuple[pl.DataFrame, int]:
    """(df, n_failures); a line that is not a JSON object is a failure."""
    rows = []
    failures = 0
    for line in lines:
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            failures += 1
            continue
        if not isinstance(row, dict):
            failures += 1
            continue
        rows.append(row)
    df = pl.DataFrame(rows, schema=SCHEMA)
    return df.with_columns(epoch_to_datetime(pl.col("t"))), failures


def write(df: pl.DataFrame) -> int:
    if df.is_empty():
        return 0
    _repo().extend(df.with_columns(pl.col("t").dt.strftime("%Y-%m").alias(_YEAR_MONTH)))
    return len(df)


def read(since: dt.datetime) -> pl.DataFrame:
    repo = _repo()
    if repo.n_files == 0:
        return pl.DataFrame()
    return (
        repo.get_full_lf()
        .filter(pl.col("t") >= since)
        .drop(_YEAR_MONTH)
        .sort("t")
        .collect()
    )


def checks(df: pl.DataFrame, a: config.Alerts) -> list[alert.Check]:
    """`a`'s thresholds on the latest sample in `df`."""
    out = []
    for r in df.filter(pl.col("t") == pl.col("t").max()).iter_rows(named=True):
        name, used, limit = r["name"], r["used"], r["limit"]
        if r["scope"] == "host":
            out.append(_fill("memory", used, limit, a.memory_pct))
        elif r["scope"] == "mount":
            out.append(_fill(f"disk {name}", used, limit, a.disk_pct))
        else:
            if limit is not None and r["anon"] is not None:
                out.append(_fill(f"anon {name}", r["anon"], limit, a.unit_anon_pct))
            if r["fd_limit"]:
                out.append(
                    _fill(
                        f"fds {name}", r["fds"], r["fd_limit"], a.unit_fds_pct, gb=False
                    )
                )
    return out


def _fill(
    key: str, used: int, limit: int, max_pct: float, gb: bool = True
) -> alert.Check:
    pct = used / limit * 100
    amount = f"{used / GB:.1f} of {limit / GB:.1f} GB" if gb else f"{used} of {limit}"
    return alert.Check(
        key, pct < max_pct, f"{amount}, {pct:.0f}% (alert at {max_pct:g}%)"
    )


def _repo() -> TableRepo:
    return TableRepo(
        config.get().metrics_dir,
        id_col="t",
        dedup_cols=["t", "scope", "name"],
        partition_cols=[_YEAR_MONTH],
        compression="zstd",
    )
