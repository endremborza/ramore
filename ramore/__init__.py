"""ramore monitoring and reporting."""

__version__ = "0.2.0"

# The monitor's User-Agent product; the report counts its probes under this UA family.
MONITOR_PRODUCT = "ramore-monitor"
# Written at the root of every rendered site: the report's freshness, read by the monitor.
SITE_STATE = "ramore.json"
