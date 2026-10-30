"""Per-bucket request counts and latency quantiles (`LEVELS`), and the sessions table."""

import datetime as dt

import polars as pl
from shackleton import TableRepo

from .. import config
from . import archive

LEVELS = {"hourly": "1h", "daily": "1d"}
GROUP_KEYS = ["bucket", "route_template", "status_family", "bot_class", "cs"]
AGG_COLS = ["t", "status", "cs", "bot_class", "urt", "size", "route_template"]


def rebuild() -> dict[str, int]:
    """Every level from the whole hot and cold archive, aggregated one day at a time (no bucket
    spans two)."""
    blocks: dict[str, list[pl.DataFrame]] = {level: [] for level in LEVELS}
    rows = 0
    for df in archive.read_days(AGG_COLS):
        rows += len(df)
        for level, every in LEVELS.items():
            blocks[level].append(_agg_block(df, every))
    out = {"rows": rows}
    for level, aggs in blocks.items():
        if not aggs:
            _repo(level).purge()
            continue
        agg = pl.concat(aggs, how="diagonal").sort("bucket")
        _repo(level).replace_all(agg)
        out[f"{level}_rows"] = len(agg)
    return out


def update(dates: list[dt.date]) -> dict[str, int]:
    """Re-aggregate the buckets of `dates` from the archive, hot or cold, keeping every other
    bucket; a store without aggregates yet is rebuilt."""
    if _repo("hourly").n_files == 0:
        return rebuild()
    new = pl.concat([archive.read_day(d, AGG_COLS) for d in dates], how="diagonal")
    out = {"rows": len(new)}
    for level, every in LEVELS.items():
        repo = _repo(level)
        kept = repo.get_full_df().filter(~pl.col("bucket").dt.date().is_in(dates))
        df = pl.concat([kept, _agg_block(new, every)], how="diagonal").sort("bucket")
        repo.replace_all(df)
        out[f"{level}_rows"] = len(df)
    return out


def load(level: str) -> pl.DataFrame:
    return _repo(level).get_full_df()


def update_sessions(sessions: pl.DataFrame) -> None:
    if not sessions.is_empty():
        _sessions_repo().extend(
            sessions.with_columns(pl.col("start").dt.date().alias("start_date"))
        )


def purge_sessions() -> None:
    _sessions_repo().purge()


def load_sessions(date_from: dt.date, columns: list[str]) -> pl.DataFrame:
    repo = _sessions_repo()
    if repo.n_files == 0:
        return pl.DataFrame()
    return (
        repo.get_full_lf()
        .filter(pl.col("start_date") >= date_from)
        .select(columns)
        .collect()
    )


def _agg_block(df: pl.DataFrame, every: str) -> pl.DataFrame:
    urt = pl.col("urt").fill_nan(None)
    return (
        df.lazy()
        .with_columns(
            pl.col("t").dt.truncate(every).alias("bucket"),
            ((pl.col("status").cast(pl.Int32) // 100).cast(pl.String) + "xx").alias(
                "status_family"
            ),
            pl.when(pl.col("cs").fill_null("") == "")
            .then(pl.lit("-"))
            .otherwise(pl.col("cs"))
            .alias("cs"),
            pl.col("bot_class").fill_null("unknown"),
        )
        .group_by(GROUP_KEYS)
        .agg(
            pl.len().alias("n"),
            pl.col("size").sum().alias("bytes"),
            urt.mean().alias("urt_mean"),
            urt.quantile(0.5).alias("urt_p50"),
            urt.quantile(0.95).alias("urt_p95"),
            urt.quantile(0.99).alias("urt_p99"),
            urt.quantile(0.999).alias("urt_p999"),
        )
        .sort("bucket")
        .collect(engine="streaming")
    )


def _repo(level: str) -> TableRepo:
    return TableRepo(config.get().aggregates_dir / level, compression="zstd")


def _sessions_repo() -> TableRepo:
    return TableRepo(
        config.get().aggregates_dir / "sessions",
        compression="zstd",
        partition_cols=["start_date"],
        dedup_cols=["session_id"],
    )
