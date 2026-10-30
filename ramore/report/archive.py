"""The request archive: one partition per day, hot within `COLD_AFTER_DAYS`, then written once into
the cold archive without `FREE_TEXT_COLUMNS`."""

import datetime as dt
from collections import Counter
from typing import Iterator

import polars as pl
from shackleton import TableRepo

from .. import config
from .parse import map_distinct
from .routes import template

COLD_AFTER_DAYS = 90

HOT_COLUMNS = [
    "t",
    "addr",
    "method",
    "path",
    "route_template",
    "status",
    "size",
    "referrer",
    "ua",
    "ua_family",
    "bot_class",
    "rt",
    "uct",
    "uht",
    "urt",
    "cs",
    "session_id",
    "referrer_domain",
]
FREE_TEXT_COLUMNS = ("path", "referrer", "ua")
COLD_COLUMNS = [c for c in HOT_COLUMNS if c not in FREE_TEXT_COLUMNS]

_YEAR_MONTH = "year_month"
_DAY = "day"
_HOT_DEDUP_COLS = ["t", "addr", "path", "status", "size", "ua"]


def annotate_routes(df: pl.DataFrame) -> pl.DataFrame:
    return map_distinct(df, "path", template, "route_template")


def write_events(df: pl.DataFrame) -> dict[str, int]:
    """Append requests to their days' partitions, deduplicated; the net new rows per day."""
    hot = _hot()
    written = {}
    for day_df in df.with_columns(pl.col("t").dt.date().alias("_date")).partition_by(
        "_date"
    ):
        d: dt.date = day_df["_date"][0]
        parts = _date_parts(d)
        before = _n_rows(hot, parts)
        hot.extend(_stamped(day_df.select(HOT_COLUMNS), parts))
        written[d.isoformat()] = _n_rows(hot, parts) - before
    return written


def rewrite_days(df: pl.DataFrame) -> None:
    """Replace each day partition `df` has rows of (read from the hot archive) with those rows."""
    hot = _hot()
    for day_df in df.partition_by(_YEAR_MONTH, _DAY):
        hot.replace_partition(day_df.select(*HOT_COLUMNS, _YEAR_MONTH, _DAY))


def list_hot_dates() -> list[dt.date]:
    return sorted(
        dt.date.fromisoformat(f"{p[_YEAR_MONTH]}-{p[_DAY]}")
        for p in _partitions(_hot())
    )


def scan_hot(
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    columns: list[str] | None = None,
) -> pl.LazyFrame | None:
    """The hot days in [date_from, date_to] as one scan, `columns` projected; None without a
    day."""
    lfs = [
        _hot().get_partition_lf(_date_parts(d))
        for d in list_hot_dates()
        if (date_from is None or d >= date_from) and (date_to is None or d <= date_to)
    ]
    if not lfs:
        return None
    return _project(pl.concat(lfs, how="diagonal"), columns)


def read_hot(
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """`scan_hot` collected: projecting at scan time keeps the free-text columns out."""
    lf = scan_hot(date_from, date_to, columns)
    return pl.DataFrame() if lf is None else lf.collect()


def read_day(d: dt.date, columns: list[str]) -> pl.DataFrame:
    """Day `d`, hot or cold."""
    parts = _date_parts(d)
    lfs = [repo.get_partition_lf(parts) for repo in (_hot(), _cold())]
    return _project(pl.concat(lfs, how="diagonal"), columns).collect()


def read_days(columns: list[str] | None = None) -> Iterator[pl.DataFrame]:
    """The whole archive one day at a time: each hot day, then each cold day."""
    for repo in (_hot(), _cold()):
        for parts in _partitions(repo):
            yield _project(repo.get_partition_lf(parts), columns).collect()


def compress_cold(today: dt.date) -> dict[str, int]:
    """Move the hot days older than `COLD_AFTER_DAYS` into the cold archive; the days moved per
    month. Each day becomes its own cold partition, so a move never reads another day. A day
    already there (a request arriving after its move, or a move interrupted before its hot purge)
    gains the hot rows it lacks."""
    cutoff = today - dt.timedelta(days=COLD_AFTER_DAYS)
    days = [d for d in list_hot_dates() if d < cutoff]
    hot, cold = _hot(), _cold()
    for d in days:
        parts = _date_parts(d)
        moved = _project(hot.get_partition_lf(parts), COLD_COLUMNS).collect()
        if _n_rows(cold, parts):
            stored = _project(cold.get_partition_lf(parts), COLD_COLUMNS).collect()
            new = moved.join(stored, on=COLD_COLUMNS, how="anti", nulls_equal=True)
            moved = pl.concat([stored, new])
        cold.replace_partition(_stamped(moved, parts))
        hot.purge_partition(parts)
    return dict(Counter(_date_parts(d)[_YEAR_MONTH] for d in days))


def _date_parts(d: dt.date) -> dict[str, str]:
    return {_YEAR_MONTH: d.strftime("%Y-%m"), _DAY: f"{d.day:02d}"}


def _partitions(repo: TableRepo) -> list[dict[str, str]]:
    """The partition values of each of `repo`'s partitions that holds data."""
    return [
        dict(part.split("=", 1) for part in d.relative_to(repo.root_path).parts)
        for d in repo._partition_dirs
    ]


def _project(lf: pl.LazyFrame, columns: list[str] | None) -> pl.LazyFrame:
    return lf if columns is None else lf.select(columns)


def _stamped(df: pl.DataFrame, parts: dict[str, str]) -> pl.DataFrame:
    return df.with_columns(pl.lit(v).alias(k) for k, v in parts.items())


def _n_rows(repo: TableRepo, parts: dict[str, str]) -> int:
    return repo.get_partition_lf(parts).select(pl.len()).collect().item()


def _hot() -> TableRepo:
    return TableRepo(
        config.get().archive_dir,
        id_col="t",
        dedup_cols=_HOT_DEDUP_COLS,
        partition_cols=[_YEAR_MONTH, _DAY],
        compression="zstd",
        compression_level=3,
    )


def _cold() -> TableRepo:
    return TableRepo(
        config.get().archive_cold_dir,
        id_col="t",
        partition_cols=[_YEAR_MONTH, _DAY],
        compression="zstd",
        compression_level=22,
    )
