import datetime as dt

import polars as pl
from shackleton import TableRepo

from .. import config
from . import archive

GROUP_KEYS = ["bucket", "route_template", "status_family", "bot_class", "cs"]

# Only these columns feed _agg_block; projecting the full-archive rebuild read to
# them keeps it from materializing the large free-text fields of every row ever.
AGG_COLS = ["t", "status", "cs", "bot_class", "urt", "size", "route_template"]


def _hourly_repo() -> TableRepo:
    return TableRepo(config.get().aggregates_dir / "hourly", compression="zstd")


def _daily_repo() -> TableRepo:
    return TableRepo(config.get().aggregates_dir / "daily", compression="zstd")


def _sessions_repo() -> TableRepo:
    return TableRepo(
        config.get().aggregates_dir / "sessions",
        compression="zstd",
        partition_cols=["start_date"],
        dedup_cols=["session_id"],
    )


def _agg_block(df: pl.DataFrame, every: str) -> pl.DataFrame:
    return (
        df.lazy()
        .with_columns(
            [
                pl.col("t").dt.truncate(every).alias("bucket"),
                ((pl.col("status").cast(pl.Int32) // 100).cast(pl.String) + "xx").alias(
                    "status_family"
                ),
                pl.when(pl.col("cs").fill_null("") == "")
                .then(pl.lit("-"))
                .otherwise(pl.col("cs"))
                .alias("cs"),
                pl.col("bot_class").fill_null("unknown"),
            ]
        )
        .group_by(GROUP_KEYS)
        .agg(
            [
                pl.len().alias("n"),
                pl.col("size").sum().alias("bytes"),
                pl.col("urt").fill_nan(None).mean().alias("urt_mean"),
                pl.col("urt").fill_nan(None).quantile(0.5).alias("urt_p50"),
                pl.col("urt").fill_nan(None).quantile(0.95).alias("urt_p95"),
                pl.col("urt").fill_nan(None).quantile(0.99).alias("urt_p99"),
                pl.col("urt").fill_nan(None).quantile(0.999).alias("urt_p999"),
            ]
        )
        .sort("bucket")
        .collect(streaming=True)
    )


def _fill_missing_cols(df: pl.DataFrame) -> pl.DataFrame:
    adds = []
    if "bot_class" not in df.columns:
        adds.append(pl.lit("unknown").alias("bot_class"))
    if "cs" not in df.columns:
        adds.append(pl.lit("-").alias("cs"))
    if "route_template" not in df.columns:
        adds.append(pl.lit("_unknown").alias("route_template"))
    return df.with_columns(adds) if adds else df


def rebuild() -> dict[str, int]:
    parts = []
    hot = archive.read_hot(columns=AGG_COLS)
    if not hot.is_empty():
        parts.append(hot)
    cold = archive.read_cold()
    if not cold.is_empty():
        parts.append(cold)
    if not parts:
        _hourly_repo().purge()
        _daily_repo().purge()
        return {"rows": 0}
    df = _fill_missing_cols(pl.concat(parts, how="diagonal"))
    hourly = _agg_block(df, "1h")
    daily = _agg_block(df, "1d")
    _hourly_repo().replace_all(hourly)
    _daily_repo().replace_all(daily)
    return {"rows": len(df), "hourly_rows": len(hourly), "daily_rows": len(daily)}


def update(affected_dates: list[dt.date]) -> dict[str, int]:
    if not affected_dates:
        return {"rows": 0, "updated_dates": 0}

    affected_set = set(affected_dates)

    parts = [
        day_df
        for d in affected_dates
        if not (day_df := archive.read_hot(date_from=d, date_to=d)).is_empty()
    ]
    new_df = (
        _fill_missing_cols(pl.concat(parts, how="diagonal"))
        if parts
        else pl.DataFrame()
    )

    def _update_one(repo: TableRepo, every: str) -> pl.DataFrame:
        existing = repo.get_full_df()
        if not existing.is_empty():
            existing = existing.filter(
                ~pl.col("bucket").dt.date().is_in(list(affected_set))
            )
        if new_df.is_empty():
            return existing
        new_agg = _agg_block(new_df, every)
        if existing.is_empty():
            return new_agg
        return pl.concat([existing, new_agg], how="diagonal").sort("bucket")

    hourly = _update_one(_hourly_repo(), "1h")
    daily = _update_one(_daily_repo(), "1d")
    _hourly_repo().replace_all(hourly)
    _daily_repo().replace_all(daily)
    return {
        "rows": len(new_df),
        "updated_dates": len(affected_dates),
        "hourly_rows": len(hourly),
        "daily_rows": len(daily),
    }


def load_hourly() -> pl.DataFrame:
    return _hourly_repo().get_full_df()


def load_daily() -> pl.DataFrame:
    return _daily_repo().get_full_df()


def _add_start_date(sessions: pl.DataFrame) -> pl.DataFrame:
    return sessions.with_columns(pl.col("start").dt.date().alias("start_date"))


def write_sessions(sessions: pl.DataFrame) -> None:
    _sessions_repo().replace_all(_add_start_date(sessions))


def purge_sessions() -> None:
    _sessions_repo().purge()


def update_sessions(new_sessions: pl.DataFrame) -> None:
    if new_sessions.is_empty():
        return
    _sessions_repo().extend(_add_start_date(new_sessions))


def load_sessions(
    date_from: dt.date | None = None, exclude: list[str] | None = None
) -> pl.DataFrame:
    repo = _sessions_repo()
    if repo.n_files == 0:
        return pl.DataFrame()
    lf = repo.get_full_lf()
    if date_from is not None:
        lf = lf.filter(pl.col("start_date") >= date_from)
    if exclude:
        lf = lf.select(pl.exclude(exclude))
    df = lf.collect()
    if "start_date" in df.columns:
        return df.drop("start_date")
    return df


def hourly_exists() -> bool:
    return _hourly_repo().n_files > 0
