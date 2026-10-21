import datetime as dt
from pathlib import Path

import polars as pl
from shackleton import TableRepo

from .. import config
from .routes import template

COLD_AFTER_DAYS = 90

_YEAR_MONTH = "year_month"
_DAY = "day"

COLD_COLUMNS = [
    "t",
    "addr",
    "method",
    "route_template",
    "status",
    "size",
    "rt",
    "urt",
    "uht",
    "uct",
    "cs",
    "session_id",
    "ua_family",
    "bot_class",
    "referrer_domain",
]

_HOT_DEDUP_COLS = ["t", "addr", "path", "status", "size", "ua"]


def _date_parts(d: dt.date) -> dict[str, str]:
    return {_YEAR_MONTH: d.strftime("%Y-%m"), _DAY: f"{d.day:02d}"}


def _parts_to_date(leaf_dir: Path) -> dt.date:
    day = leaf_dir.name.removeprefix(f"{_DAY}=")
    ym = leaf_dir.parent.name.removeprefix(f"{_YEAR_MONTH}=")
    return dt.date.fromisoformat(f"{ym}-{day.zfill(2)}")


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


def add_date_partition_cols(df: pl.DataFrame) -> pl.DataFrame:
    """Pre-add year_month/day cols derived from t so they precede annotation cols."""
    return df.with_columns(
        [
            pl.col("t")
            .dt.convert_time_zone("UTC")
            .dt.strftime("%Y-%m")
            .alias(_YEAR_MONTH),
            pl.col("t").dt.convert_time_zone("UTC").dt.strftime("%d").alias(_DAY),
        ]
    )


def annotate_routes(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.String).alias("route_template"))
    return df.with_columns(
        pl.col("path")
        .map_elements(template, return_dtype=pl.String)
        .alias("route_template")
    )


def write_events(df: pl.DataFrame) -> dict[str, int]:
    """Append events to per-day partitions, deduped. Returns net-new counts per date."""
    if df.is_empty():
        return {}
    df = df.with_columns(
        pl.col("t").dt.convert_time_zone("UTC").dt.date().alias("__date")
    )
    hot = _hot()
    written: dict[str, int] = {}
    for day_df in df.partition_by("__date", maintain_order=False):
        d = day_df["__date"][0]
        parts = _date_parts(d)
        prev_n = len(hot.get_partition_df(parts))
        hot.extend(
            day_df.drop("__date").with_columns(
                pl.lit(parts[_YEAR_MONTH]).alias(_YEAR_MONTH),
                pl.lit(parts[_DAY]).alias(_DAY),
            )
        )
        new_n = len(hot.get_partition_df(parts))
        written[d.isoformat()] = new_n - prev_n
    return written


def list_hot_dates() -> list[dt.date]:
    return sorted(
        _parts_to_date(d)
        for d in _hot()._partition_dirs
        if d.name.startswith(f"{_DAY}=") and d.parent.name.startswith(f"{_YEAR_MONTH}=")
    )


def list_cold_months() -> list[tuple[int, int]]:
    result = []
    for d in _cold()._partition_dirs:
        month_part, year_part = d.name, d.parent.name
        if month_part.startswith("month=") and year_part.startswith("year="):
            result.append((int(year_part[5:]), int(month_part[6:])))
    return sorted(result)


def read_hot(
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """Read the hot window. ``columns`` restricts the projection at scan time so
    the multi-million-row window never materializes its large free-text fields."""
    repo = _hot()
    if date_from is None and date_to is None:
        if repo.n_files == 0:
            return pl.DataFrame()
        lf = repo.get_full_lf()
    else:
        lfs = [
            repo.get_partition_lf(_date_parts(d))
            for d in list_hot_dates()
            if (date_from is None or d >= date_from)
            and (date_to is None or d <= date_to)
        ]
        if not lfs:
            return pl.DataFrame()
        lf = pl.concat(lfs)
    if columns is not None:
        lf = lf.select(columns)
    return lf.collect()


def read_cold(
    date_from: dt.date | None = None, date_to: dt.date | None = None
) -> pl.DataFrame:
    months = [
        (y, m)
        for y, m in list_cold_months()
        if (date_from is None or (y, m) >= (date_from.year, date_from.month))
        and (date_to is None or (y, m) <= (date_to.year, date_to.month))
    ]
    if not months:
        return pl.DataFrame()
    parts = [
        _cold().get_partition_df({"year": str(y), "month": str(m)}) for y, m in months
    ]
    return pl.concat(parts)


def compress_cold(today: dt.date) -> dict[str, int]:
    """Move per-day files older than COLD_AFTER_DAYS into per-month cold archive."""
    cutoff = today - dt.timedelta(days=COLD_AFTER_DAYS)
    by_month: dict[tuple[int, int], list[dt.date]] = {}
    for d in list_hot_dates():
        if d >= cutoff:
            continue
        by_month.setdefault((d.year, d.month), []).append(d)
    result = {}
    for (y, m), dates in by_month.items():
        day_frames: list[pl.DataFrame] = []
        missing_cols_dates: list[str] = []
        for d in dates:
            day = _hot().get_partition_df(_date_parts(d))
            if not all(c in day.columns for c in COLD_COLUMNS):
                missing_cols_dates.append(d.isoformat())
                continue
            day_frames.append(day.select(COLD_COLUMNS))
        if missing_cols_dates:
            result[f"{y}-{m:02d}-skipped"] = len(missing_cols_dates)
            continue
        if not day_frames:
            continue
        cold_df = pl.concat(day_frames).with_columns(
            [pl.lit(y).alias("year"), pl.lit(m).alias("month")]
        )
        _cold().extend(cold_df)
        for d in dates:
            _hot().purge_partition(_date_parts(d))
        result[f"{y}-{m:02d}"] = len(day_frames)
    return result


def rewrite_hot(d: dt.date, df: pl.DataFrame) -> None:
    parts = _date_parts(d)
    for col, val in parts.items():
        if col not in df.columns:
            df = df.with_columns(pl.lit(val).alias(col))
    _hot().replace_partition(df)
