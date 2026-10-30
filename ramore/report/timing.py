import time
from contextlib import contextmanager
from typing import Generator

_timings: dict[str, float] = {}


def get() -> dict[str, float]:
    return dict(_timings)


def reset() -> None:
    _timings.clear()


@contextmanager
def timed(key: str) -> Generator[None, None, None]:
    """Time the block under `key`; a key timed again (a chunk loop) adds up."""
    t0 = time.time()
    yield
    _timings[key] = round(_timings.get(key, 0) + time.time() - t0, 3)
