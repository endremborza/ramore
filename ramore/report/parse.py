import json
from typing import Iterable

import polars as pl

from .. import config
from ..box import REQUEST_FIELDS

_KEYS = frozenset(REQUEST_FIELDS)

COLUMNS = {
    "t": pl.Datetime("us", "UTC"),
    "addr": pl.String,
    "method": pl.String,
    "path": pl.String,
    "status": pl.UInt16,
    "size": pl.UInt32,
    "referrer": pl.String,
    "ua": pl.String,
    "rt": pl.Float32,
    "uct": pl.Float32,
    "uht": pl.Float32,
    "urt": pl.Float32,
    "cs": pl.String,
    "host": pl.String,
}


def parse_lines(lines: Iterable[str]) -> tuple[pl.DataFrame, int]:
    """Request-log lines (`box.REQUEST_FIELDS`) as typed rows; (df, n_failures).

    A line that is not a JSON object with exactly the request keys, or whose time does not
    parse, is a failure. Upstream times that are not one number (`-`, or a list after a retry)
    are null. Empty lines are skipped."""
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
        if not isinstance(row, dict) or row.keys() != _KEYS:
            failures += 1
            continue
        rows.append(row)
    raw = pl.DataFrame(rows, schema={k: pl.String for k in COLUMNS})
    df = raw.with_columns(
        epoch_to_datetime(pl.col("t").cast(pl.Float64, strict=False)),
        *(
            pl.col(k).cast(dtype, strict=False)
            for k, dtype in COLUMNS.items()
            if k != "t" and dtype != pl.String
        ),
    )
    n_bad_time = int(df["t"].null_count())
    if n_bad_time:
        df = df.filter(pl.col("t").is_not_null())
    return df, failures + n_bad_time


def epoch_to_datetime(seconds: pl.Expr) -> pl.Expr:
    return (seconds * 1_000_000).round(0).cast(pl.Int64).cast(pl.Datetime("us", "UTC"))


def keep_live_hosts(df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Keep only rows served to the app's hosts; drop the transient `host` column.

    The box's log can mix the app's traffic with other vhosts and junk hitting it by raw IP or
    a spoofed Host, so the filter is an allowlist: a denylist cannot name the junk. `host` is
    used only at ingest and never persisted, keeping the archive schema stable."""
    keep = pl.col("host").str.to_lowercase().is_in(list(config.get().hosts))
    n_dropped = int(df.select((~keep).sum()).item())
    return df.filter(keep).drop("host"), n_dropped
