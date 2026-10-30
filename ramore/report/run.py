"""`ramore report`: one pull → archive → aggregate → render → publish run."""

import datetime as dt
import json
import sys
import time
import traceback
from contextlib import contextmanager
from typing import Iterator, Literal

import polars as pl

from .. import alert, box, config
from . import (
    aggregate,
    archive,
    metrics,
    parse,
    publish,
    pull,
    render,
    routes,
    sessions,
    state,
    timing,
)
from .classify import annotate_events, classify_sessions

Mode = Literal["local", "public", "both"]
TOP_UNMATCHED = 20
LINE_COUNTS = ("lines_fetched", "events_parsed", "parse_failures", "non_live_dropped")


def main(
    *,
    no_pull: bool = False,
    no_publish: bool = False,
    render_only: bool = False,
    dry_run: bool = False,
    reclassify_all: bool = False,
    mode: Mode = "public",
    config_file: str = config.CONFIG_NAME,
) -> None:
    """Pull the new log lines, archive, aggregate, render and publish the report.

    A pull first installs or updates ramore's side of the box when it is not current (`ramore
    box`). --no-pull aggregates and renders what the store holds; --render-only skips aggregation
    too; --dry-run pulls and parses with no writes, the box included; --reclassify-all
    re-classifies every hot day and rebuilds the aggregates before rendering; --no-publish keeps
    the site local, as does an app without a [publish] section.
    """
    config.use(config.load(config_file)).ensure_dirs()
    with run_record() as rec:
        if dry_run:
            _dry_run(rec)
            return
        if reclassify_all:
            _reclassify_all(rec)
        elif not render_only and no_pull:
            with timing.timed("aggregate"):
                rec["aggregates"] = aggregate.rebuild()
        elif not render_only:
            _pull(rec)
        do_render(mode, rec, push=not no_publish)


@contextmanager
def run_record() -> Iterator[dict]:
    """A run's record (counts, timings, errors), appended to the day's run log once it ends."""
    rec: dict = {"ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")}
    timing.reset()
    started = time.time()
    try:
        yield rec
        rec["status"] = "ok"
    except Exception as exc:
        rec |= {"status": "error", "error": repr(exc), "trace": traceback.format_exc()}
        raise
    finally:
        if "top_unmatched" in rec:
            top = sorted(rec["top_unmatched"].items(), key=lambda kv: (-kv[1], kv[0]))
            rec["top_unmatched"] = dict(top[:TOP_UNMATCHED])
        rec["duration_s"] = round(time.time() - started, 2)
        rec["timings"] = timing.get()
        line = json.dumps(rec, default=str)
        log = config.get().run_logs_dir / f"run-{dt.date.today().isoformat()}.log"
        with log.open("a") as f:
            f.write(line + "\n")
        print(line, file=sys.stderr)


def ingest(lines: list[str], s: state.State, rec: dict) -> list[dt.date]:
    """Request-log lines into the archive and the sessions table, their counts added to `rec`;
    the days they wrote to."""
    with timing.timed("ingest.parse"):
        df = _parse(lines, rec)
    if df.is_empty():
        return []
    with timing.timed("ingest.sessions"):
        df = sessions.assign_sessions(df)
    with timing.timed("ingest.classify"):
        sess = classify_sessions(df)
        aggregate.update_sessions(sess)
        df = annotate_events(df, sess)
    with timing.timed("ingest.write"):
        written = archive.write_events(df)
    _add(rec, {"written_per_day": written})
    with timing.timed("ingest.compress_cold"):
        if cold := archive.compress_cold(today=dt.date.today()):
            _add(rec, {"cold_compaction": cold})
    s.last_event_ts = str(df["t"].max())
    return [dt.date.fromisoformat(d) for d in written]


def do_render(mode: Mode, rec: dict, push: bool = True) -> None:
    """Render the `mode` sites; publish the public one when the app has a [publish] section."""
    modes: list[render.Mode] = ["local", "public"] if mode == "both" else [mode]
    rec["rendered"] = modes
    for m in modes:
        render.render_all(m)
    if not push or "public" not in modes or config.get().publish is None:
        return
    with timing.timed("publish"):
        try:
            rec["published"] = publish.publish()
        except Exception as exc:
            rec["published"] = False
            rec["publish_error"] = repr(exc)


def _pull(rec: dict) -> None:
    """The box's new request lines, a chunk at a time: each chunk is archived, its days
    re-aggregated and its offset saved before the next is read, so a backlog costs one chunk's
    memory and an interrupted run resumes after its last saved chunk."""
    cfg = config.get()
    with timing.timed("box"):
        rec["box_installed"] = box.ensure(cfg)
    s = state.load()
    if cfg.metrics is not None:
        with timing.timed("pull.metrics"):
            _pull_metrics(s, rec)
    rec["aggregates"] = {"rows": 0}
    rows, days = 0, set()
    chunks = _request_chunks(s, rec)
    while True:
        with timing.timed("pull.requests"):
            chunk = next(chunks, None)
        if chunk is None:
            break
        if written := ingest(chunk.lines, s, rec):
            with timing.timed("aggregate"):
                agg = aggregate.update(written)
            rows, days = rows + agg["rows"], days | set(written)
            rec["aggregates"] = agg | {"rows": rows, "updated_dates": len(days)}
        s.offsets[box.REQUEST_LOG] = chunk.offset
        state.save(s)
    s.last_run = state.now_iso()
    state.save(s)


def _dry_run(rec: dict) -> None:
    rec["box_current"] = box.is_current(config.get())
    span = []
    for chunk in _request_chunks(state.load(), rec):
        df = _parse(chunk.lines, rec)
        if not df.is_empty():
            span += [df["t"].min(), df["t"].max()]
    if span:
        rec["t_min"], rec["t_max"] = str(min(span)), str(max(span))


def _request_chunks(s: state.State, rec: dict) -> Iterator[pull.FetchResult]:
    """The request log's new chunks, starting `rec`'s line counts and recording whether one read
    a rotated log."""
    rec |= dict.fromkeys(LINE_COUNTS, 0) | {"rotated": False}
    for chunk in pull.fetch_chunks(box.REQUEST_LOG, s.offset(box.REQUEST_LOG)):
        rec["rotated"] |= chunk.rotated
        yield chunk


def _parse(lines: list[str], rec: dict) -> pl.DataFrame:
    """The app's requests among `lines`, typed and templated; their counts go to `rec`."""
    df, failures = parse.parse_lines(lines)
    df, dropped = parse.keep_live_hosts(df)
    df = archive.annotate_routes(df)
    unmatched = df.filter(pl.col("route_template") == routes.UNKNOWN)["path"]
    _add(rec, dict(zip(LINE_COUNTS, (len(lines), len(df), failures, dropped))))
    if len(unmatched):
        _add(rec, {"top_unmatched": dict(unmatched.value_counts().iter_rows())})
    return df


def _add(rec: dict, counts: dict) -> None:
    """Add a chunk's counts to its run's record key by key, a nested table per key."""
    for k, v in counts.items():
        if isinstance(v, dict):
            _add(rec.setdefault(k, {}), v)
        else:
            rec[k] = rec.get(k, 0) + v


def _pull_metrics(s: state.State, rec: dict) -> None:
    fetched = pull.fetch_new_lines(box.METRICS_LOG, s.offset(box.METRICS_LOG))
    df, failures = metrics.parse_lines(fetched.lines)
    rec["metrics_rows"] = metrics.write(df)
    rec["metrics_parse_failures"] = failures
    s.offsets[box.METRICS_LOG] = fetched.offset
    if not df.is_empty():
        checks = metrics.checks(df, config.get().alerts)
        try:
            events = alert.run_step("report", checks, time.time(), sustain_s=0)
        except alert.MailError as exc:
            # the report goes on; the unsaved incident table mails these events next run
            rec["alert_error"] = str(exc)
        else:
            rec["alerts"] = [f"{e.kind} {e.key}: {e.detail}" for e in events]


def _reclassify_all(rec: dict) -> None:
    dates = archive.list_hot_dates()
    n_sessions = 0
    aggregate.purge_sessions()
    with timing.timed("reclassify"):
        for d in dates:
            df = archive.read_hot(d, d)
            sess = classify_sessions(df)
            aggregate.update_sessions(sess)
            archive.rewrite_days(annotate_events(df, sess))
            n_sessions += len(sess)
    rec["reclassified_dates"] = len(dates)
    rec["reclassified_sessions"] = n_sessions
    with timing.timed("aggregate"):
        rec["aggregates"] = aggregate.rebuild()
