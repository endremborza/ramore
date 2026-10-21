import re
from typing import Iterable

import polars as pl

from .. import config

LOG_TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"

# The `log_format` in docs/report.md. `cs=` and `host=` always trail the line (cs may be empty
# when no upstream cache was consulted). A torn/interleaved line whose bracketed time cannot be
# parsed still matches here; it is dropped downstream in parse_lines, not rejected here.
LINE_RE = re.compile(
    r"^(?P<addr>\S+) - \S+ "
    r"\[(?P<time>[^\]]+)\] "
    r'"(?P<method>[A-Z]+) (?P<path>[^"]*?) [^"]*?" '
    r"(?P<status>\d{3}) (?P<size>\d+) "
    r'"(?P<referrer>[^"]*)" '
    r'"(?P<ua>[^"]*)"'
    r"rt=(?P<rt>\S+) "
    r'uct="(?P<uct>[^"]*)" '
    r'uht="(?P<uht>[^"]*)" '
    r'urt="(?P<urt>[^"]*)" '
    r"cs=(?P<cs>\S*) "
    r"host=(?P<host>\S+)$"
)


def _f(s: str | None) -> float | None:
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_lines(lines: Iterable[str]) -> tuple[pl.DataFrame, int]:
    """Return (df, n_failures). Drops empty lines silently."""
    rows = []
    failures = 0
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            failures += 1
            continue
        rows.append(m.groupdict())
    if not rows:
        return _empty_df(), failures

    raw = pl.DataFrame(rows)
    df = (
        raw.with_columns(
            [
                # non-strict: a torn/interleaved line can carry junk in the time
                # bracket (e.g. a client IP spliced in). Parse it to null and drop
                # the row below rather than raising — a raise would abort the whole
                # batch and echo the offending raw values (IPs) into the run log.
                pl.col("time")
                .str.strptime(pl.Datetime("us", "UTC"), LOG_TIME_FMT, strict=False)
                .alias("t"),
                pl.col("status").cast(pl.UInt16),
                pl.col("size").cast(pl.UInt32, strict=False),
                pl.col("rt").map_elements(_f, return_dtype=pl.Float32),
                pl.col("uct").map_elements(_f, return_dtype=pl.Float32),
                pl.col("uht").map_elements(_f, return_dtype=pl.Float32),
                pl.col("urt").map_elements(_f, return_dtype=pl.Float32),
                pl.when(pl.col("referrer") == "-")
                .then(pl.lit(""))
                .otherwise(pl.col("referrer"))
                .alias("referrer"),
                pl.when(pl.col("ua") == "-")
                .then(pl.lit(""))
                .otherwise(pl.col("ua"))
                .alias("ua"),
                pl.col("cs").map_elements(
                    lambda s: "" if s in ("-", None) else s, return_dtype=pl.String
                ),
            ]
        )
        .drop("time")
        .select(
            [
                "t",
                "addr",
                "method",
                "path",
                "status",
                "size",
                "referrer",
                "ua",
                "rt",
                "uct",
                "uht",
                "urt",
                "cs",
                "host",
            ]
        )
    )

    n_bad_time = int(df["t"].null_count())
    if n_bad_time:
        df = df.filter(pl.col("t").is_not_null())
        failures += n_bad_time
    return df, failures


def keep_live_hosts(df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Keep only rows served to the app's hosts; drop the transient `host` column.

    The box's log can mix the app's traffic with other vhosts and junk hitting it by raw IP or
    a spoofed Host, so the filter is an allowlist: a denylist cannot name the junk. `host` is
    used only at ingest and never persisted, keeping the archive schema stable."""
    if "host" not in df.columns:
        return df, 0
    keep = pl.col("host").str.to_lowercase().is_in(list(config.get().hosts))
    n_dropped = int(df.select((~keep).sum()).item())
    return df.filter(keep).drop("host"), n_dropped


def _empty_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "t": pl.Series([], dtype=pl.Datetime("us", "UTC")),
            "addr": pl.Series([], dtype=pl.String),
            "method": pl.Series([], dtype=pl.String),
            "path": pl.Series([], dtype=pl.String),
            "status": pl.Series([], dtype=pl.UInt16),
            "size": pl.Series([], dtype=pl.UInt32),
            "referrer": pl.Series([], dtype=pl.String),
            "ua": pl.Series([], dtype=pl.String),
            "rt": pl.Series([], dtype=pl.Float32),
            "uct": pl.Series([], dtype=pl.Float32),
            "uht": pl.Series([], dtype=pl.Float32),
            "urt": pl.Series([], dtype=pl.Float32),
            "cs": pl.Series([], dtype=pl.String),
            "host": pl.Series([], dtype=pl.String),
        }
    )
