"""The request archive: one hot partition per day within `COLD_AFTER_DAYS`, older days merged per
month into the cold archive without `FREE_TEXT_COLUMNS`."""

import datetime as dt

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
        hot.extend(
            day_df.select(HOT_COLUMNS).with_columns(
                pl.lit(v).alias(k) for k, v in parts.items()
            )
        )
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


def read_hot(
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """The hot days in [date_from, date_to]; `columns` projects at scan time, so a long window
    never materializes the free-text columns."""
    lfs = [
        _hot().get_partition_lf(_date_parts(d))
        for d in list_hot_dates()
        if (date_from is None or d >= date_from) and (date_to is None or d <= date_to)
    ]
    if not lfs:
        return pl.DataFrame()
    lf = pl.concat(lfs, how="diagonal")
    return (lf if columns is None else lf.select(columns)).collect()


def read_cold(columns: list[str]) -> pl.DataFrame:
    cold = _cold()
    if cold.n_files == 0:
        return pl.DataFrame()
    return cold.get_full_lf().select(columns).collect()


def compress_cold(today: dt.date) -> dict[str, int]:
    """Move the hot days older than `COLD_AFTER_DAYS` into their months' cold partitions; the
    days moved per month."""
    cutoff = today - dt.timedelta(days=COLD_AFTER_DAYS)
    by_month: dict[tuple[int, int], list[dt.date]] = {}
    for d in list_hot_dates():
        if d < cutoff:
            by_month.setdefault((d.year, d.month), []).append(d)
    hot, cold = _hot(), _cold()
    for (y, m), days in by_month.items():
        frames = [
            hot.get_partition_df(_date_parts(d)).select(COLD_COLUMNS) for d in days
        ]
        cold.extend(
            pl.concat(frames).with_columns(
                pl.lit(y).alias("year"), pl.lit(m).alias("month")
            )
        )
        for d in days:
            hot.purge_partition(_date_parts(d))
    return {f"{y}-{m:02d}": len(days) for (y, m), days in by_month.items()}


def _date_parts(d: dt.date) -> dict[str, str]:
    return {_YEAR_MONTH: d.strftime("%Y-%m"), _DAY: f"{d.day:02d}"}


def _partitions(repo: TableRepo) -> list[dict[str, str]]:
    """The partition values of each of `repo`'s partitions that holds data."""
    return [
        dict(part.split("=", 1) for part in d.relative_to(repo.root_path).parts)
        for d in repo._partition_dirs
    ]


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
        partition_cols=["year", "month"],
        compression="zstd",
        compression_level=22,
    )
