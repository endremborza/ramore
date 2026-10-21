import time
from contextlib import contextmanager
from typing import Generator

_timings: dict[str, float] = {}


def get() -> dict[str, float]:
    return dict(_timings)


@contextmanager
def timed(key: str) -> Generator[None, None, None]:
    t0 = time.time()
    yield
    _timings[key] = round(time.time() - t0, 3)
