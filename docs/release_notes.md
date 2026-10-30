## v0.2.2

- Bump the required shackleton version to 2.1.0.

---

## v0.2.1

- The request log is now pulled in bounded chunks, each chunk's offset saved as it's archived, so a backlog after downtime costs time rather than memory and an interrupted run resumes from its last saved chunk; the report unit's memory cap drops from 24G to 10G accordingly.
- A failed alert mail (missing SMTP setting or a failed send) no longer crashes the run — it's recorded as `alert_error` in the run log (kept out of the public site) and the report continues.
- The cold archive now partitions by day instead of merging a whole month, so compacting old days reads and rewrites only the days actually being moved.
- Rendering keeps the hot window and its sessions as lazy scans throughout, streaming each page's own aggregation instead of collecting the whole window, improving memory use on large sites.

---

## v0.2.0

First PyPI release. ramore report turns an nginx request log and box metrics into a static report site: per-rule bot classification, hot/cold archive, published to a pages branch by git plumbing. Also ramore box, monitor, units and demo. Requires polars 2.
