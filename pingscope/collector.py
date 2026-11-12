"""Measurement loop: every interval, resolve targets, run all probes in parallel, store results."""
import asyncio
import logging
import time
import zlib

from . import settings as settings_mod
from .alerts import Alerter
from .db import measure_every, summary
from .mtr import MtrScheduler
from .probes import PROBES, PartialFailure, Result
from .resolver import Resolver

log = logging.getLogger("pingscope")


class Collector:
    def __init__(self, store):
        self.store = store
        self.resolver = Resolver(store)  # remembers the last good address of hostnames across restarts
        self.mtr = MtrScheduler(store)
        self.alerter = Alerter(store)
        self._mail_task = None
        self.series = {}      # target id -> [Series]
        self.latest = {}      # series key -> summary
        self.last_run = None
        self.last_duration = None
        self.warnings = []
        self._wake = asyncio.Event()

    def poke(self):
        """Run a round soon after a configuration change (new targets show up quickly)."""
        self._wake.set()

    async def run(self):
        await self.mtr.init()
        self.latest = await asyncio.to_thread(self.store.latest_all, int(time.time()) - 3600)
        await self.plan_only()
        while True:
            interval = self.store.settings()["interval"]
            now = time.time()
            nxt = (int(now) // interval + 1) * interval
            try:
                await asyncio.wait_for(self._wake.wait(), nxt - now)
                self._wake.clear()
                await self.plan_only()  # config changed: refresh what the UI shows, measure at the boundary
                continue
            except asyncio.TimeoutError:
                pass
            try:
                await self.cycle(nxt)
            except Exception:
                log.exception("Measurement round failed")

    async def plan_only(self):
        s = self.store.settings()
        targets = await asyncio.to_thread(self.store.targets, True)
        series = await self.resolver.plan(targets, s["resolve_interval"])
        self._set_series(series)

    def _set_series(self, series):
        by = {}
        for x in series:
            by.setdefault(x.target_id, []).append(x)
        self.series = by

    async def cycle(self, ts):
        t0 = time.monotonic()
        s = self.store.settings()
        self.warnings = settings_mod.check(s)
        targets = await asyncio.to_thread(self.store.targets, True)
        series = await self.resolver.plan(targets, s["resolve_interval"])
        self._set_series(series)
        self.warnings += too_long(series, s)
        groups = {g["id"]: g for g in await asyncio.to_thread(self.store.groups)}
        due = [x for x in series if is_due(x.target, groups, s, ts)]  # targets measured every N rounds skip the rest
        results, errors = await measure_spread(due, s, ts)
        self.warnings += [f"The {p} probe could not run in the last round, so no data was recorded for it: {e}"
                          for p, e in errors.items()]
        rows = []
        for x in series:
            r = results.get(x.key)
            if r is None:  # probe failed locally: leave a gap instead of recording fake packet loss
                continue
            rows.append((x.key, r.sent, r.lost, r.rtts))
            self.latest[x.key] = summary(ts, r.sent, r.lost, r.rtts)
        await asyncio.to_thread(self.store.insert_samples, ts, rows)
        self.mtr.schedule(ts, series, results, s)
        try:
            events = await asyncio.to_thread(self.alerter.check, ts, series, results, s)
            if events:  # send in the background, the next round doesn't wait for the mail server
                self._mail_task = asyncio.create_task(self.alerter.notify(ts, events, s))
        except Exception:
            log.exception("Checking alerts failed")
        await asyncio.to_thread(self.store.rollup, ts, s["raw_retention_days"] * 86400)
        self.last_run = ts
        self.last_duration = round(time.monotonic() - t0, 1)
        log.info("Round %s: %d series in %.1fs", time.strftime("%H:%M", time.localtime(ts)), len(rows), self.last_duration)


def is_due(target, groups, s, ts):
    """Whether a target is measured in the round starting at ts. A target measured every N rounds gets its own fixed
    round within each N, so not all of them fall in the same round."""
    n = measure_every(target, groups)
    if n <= 1:
        return True
    return (ts // s["interval"] + zlib.crc32(f"pingscope-every-{target['id']}".encode())) % n == 0


def start_offset(series, s):
    """Seconds after the start of the round at which this series is measured.

    "start": at the beginning of the round. "random" (like SmokePing's offset = random): a fixed moment per target,
    spread over the part of the round that still leaves time to finish before the next round. Whole seconds, so
    targets with the same moment share one fping process.
    """
    t = series.target
    if (t.get("start_offset") or s["start_offset"]) != "random" or t["probe"] not in PROBES:
        return 0
    room = s["interval"] * 0.9 - PROBES[t["probe"]].duration(series, s) - 2
    if room < 1:
        return 0
    return int(zlib.crc32(f"pingscope-offset-{t['id']}".encode()) % 10000 / 10000 * room)


def too_long(series, s):
    """Warnings for targets whose own probe settings don't fit in the measurement interval."""
    out, seen = [], set()
    for x in series:
        p = PROBES.get(x.target["probe"])
        if p and x.target["id"] not in seen and p.duration(x, s) > s["interval"] * 0.9:
            seen.add(x.target["id"])
            out.append(f"{x.target['name']}: probes per round × spacing ({p.duration(x, s):.0f}s) is too long for the "
                       f"{s['interval']}s interval; rounds may overlap. Lower its probes or spacing.")
    return out[:10]


async def measure_spread(series, s, ts):
    """Measure all series, each starting at its own offset after `ts` (the start of the round)."""
    by_offset = {}
    for x in series:
        by_offset.setdefault(start_offset(x, s), []).append(x)

    async def batch(offset, xs):
        delay = ts + offset - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        return await measure(xs, s)

    results, errors = {}, {}
    for res, err in await asyncio.gather(*[batch(o, xs) for o, xs in sorted(by_offset.items())]):
        results.update(res)
        for p, e in err.items():
            errors.setdefault(p, e)
    return results, errors


async def measure(series, s):
    """Returns (results, errors). Series of a probe type that failed to run are absent from results."""
    by_probe = {}
    for x in series:
        by_probe.setdefault(x.target["probe"], []).append(x)
    names = [p for p in by_probe if p in PROBES]
    outs = await asyncio.gather(*[PROBES[p].run(by_probe[p], s) for p in names], return_exceptions=True)
    results, errors = {}, {}
    for p, out in zip(names, outs):
        if isinstance(out, PartialFailure):
            log.error("Probe %s partly failed: %s", p, out)
            errors[p] = str(out)
            results.update(out.results)
            continue
        if isinstance(out, Exception):
            log.error("Probe %s failed: %s", p, out)
            errors[p] = str(out) or out.__class__.__name__
            continue
        results.update(out)
    for x in series:
        if x.key not in results and x.target["probe"] not in errors:
            cnt = PROBES[x.target["probe"]].count(x, s) if x.target["probe"] in PROBES else 1
            results[x.key] = Result(cnt, cnt, [])
    return results, errors
