import asyncio
import time
from typing import NamedTuple


class Result(NamedTuple):
    sent: int
    lost: int
    rtts: list  # milliseconds of the probes that got a reply


PROBES = {}


class PartialFailure(Exception):
    """Raised by a probe when only part of its measurements could run. `results` holds what did work."""

    def __init__(self, results, message):
        super().__init__(message)
        self.results = results


def register(cls):
    PROBES[cls.name] = cls()
    return cls


class Probe:
    name = ""
    label = ""
    description = ""
    count_setting = "ping_count"
    fields = ()           # extra target fields shown in the admin form: "dns_query", "dns_type", "port"
    default_port = None

    def count(self, series, s):
        return series.target.get("count") or s[self.count_setting]

    # per-target overrides of the global settings (None = use the setting)
    def spacing_ms(self, series, s):
        return series.target.get("spacing_ms") or s["probe_spacing_ms"]

    def timeout_ms(self, series, s):
        return series.target.get("timeout_ms") or s["timeout_ms"]

    def duration(self, series, s):
        """Worst-case seconds one round of this probe takes for a series."""
        return (self.count(series, s) * self.spacing_ms(series, s) + self.timeout_ms(series, s)) / 1000

    async def run(self, series, s):
        """Measure all given series once. Returns {series.key: Result}."""
        raise NotImplementedError

    def info(self):
        return {"name": self.name, "label": self.label, "description": self.description,
                "fields": list(self.fields), "default_port": self.default_port}


async def spaced(n, spacing_s, once):
    """Run `once()` n times, starting one every spacing_s seconds (overlapping if needed). Returns list of rtt|None."""
    start = time.monotonic()

    async def one(i):
        await asyncio.sleep(max(0.0, start + i * spacing_s - time.monotonic()))
        return await once()

    return await asyncio.gather(*[one(i) for i in range(n)])


def to_result(values):
    rtts = [round(v, 3) for v in values if v is not None]
    return Result(len(values), len(values) - len(rtts), rtts)
