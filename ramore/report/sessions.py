import datetime as dt
import hashlib

import polars as pl

from .state import get_or_create_salt

SESSION_IDLE_MIN = 30
SESSION_ID_LEN = 12


def assign_sessions(df: pl.DataFrame) -> pl.DataFrame:
    """`df` with `session_id`: an `(addr, ua)` pair's requests, split by a gap of more than
    `SESSION_IDLE_MIN`; the id hashes the pair and the session's start with the start day's salt."""
    new = (
        (pl.col("addr") != pl.col("addr").shift())
        | (pl.col("ua") != pl.col("ua").shift())
        | (pl.col("t").diff().dt.total_minutes() > SESSION_IDLE_MIN)
    ).fill_null(True)
    df = df.sort("addr", "ua", "t").with_columns(
        pl.when(new).then(pl.col("t")).forward_fill().alias("_start")
    )
    keys = df.select("addr", "ua", "_start").unique()
    salts = {d: get_or_create_salt(d.isoformat()) for d in keys["_start"].dt.date()}
    ids = [_hash(salts[s.date()], a, u, s) for a, u, s in keys.iter_rows()]
    keys = keys.with_columns(pl.Series("session_id", ids, dtype=pl.String))
    return df.join(keys, on=["addr", "ua", "_start"], how="left").drop("_start")


def _hash(salt: str, addr: str, ua: str, start: dt.datetime) -> str:
    h = hashlib.sha256(f"{salt}|{addr}|{ua}|{start.isoformat()}".encode()).hexdigest()
    return h[:SESSION_ID_LEN]
