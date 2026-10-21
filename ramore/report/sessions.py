import datetime as dt
import hashlib

import polars as pl

from .state import get_or_create_salt

SESSION_IDLE_MIN = 30
SESSION_ID_LEN = 12


def assign_sessions(df: pl.DataFrame) -> pl.DataFrame:
    """Adds `session_id` to df. Sessions = same (addr, ua) split by >SESSION_IDLE_MIN gap."""
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.String).alias("session_id"))

    df = df.sort(["addr", "ua", "t"])

    # Detect new-session boundaries
    same_who = (pl.col("addr") == pl.col("addr").shift(1)) & (
        pl.col("ua") == pl.col("ua").shift(1)
    )
    gap = pl.col("t").diff().dt.total_minutes() > SESSION_IDLE_MIN
    new_session = (~same_who) | gap
    # First row is always a new session
    df = df.with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit(True))
        .otherwise(new_session)
        .alias("_new_session")
    )

    # Propagate session start time forward
    df = df.with_columns(
        pl.when(pl.col("_new_session"))
        .then(pl.col("t"))
        .otherwise(None)
        .forward_fill()
        .alias("_start_t")
    )

    # Build salts per date
    dates = df["_start_t"].dt.date().unique().to_list()
    salts = {d.isoformat(): get_or_create_salt(d.isoformat()) for d in dates}

    session_ids = [
        _hash_session(salts[start_t.date().isoformat()], addr, ua, start_t)
        for start_t, addr, ua in zip(
            df["_start_t"].to_list(),
            df["addr"].to_list(),
            df["ua"].to_list(),
        )
    ]

    return df.with_columns(pl.Series("session_id", session_ids)).drop(
        ["_new_session", "_start_t"]
    )


def _hash_session(salt: str, addr: str, ua: str, start: dt.datetime) -> str:
    h = hashlib.sha256(f"{salt}|{addr}|{ua}|{start.isoformat()}".encode()).hexdigest()
    return h[:SESSION_ID_LEN]
