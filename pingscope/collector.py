"""Measurement loop: every interval, resolve targets, run all probes in parallel, store results."""
import asyncio
import logging
import time

from . import settings as settings_mod
from .db import summary
from .mtr import MtrScheduler
from .probes import PROBES, Result
from .resolver import Resolver

log = logging.getLogger("pingscope")


class Collector:
    def __init__(self, store):
        self.store = store
        self.resolver = Resolver()
        self.mtr = MtrScheduler(store)
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
        results = await measure(series, s)
        rows = []
        for x in series:
            r = results[x.key]
            rows.append((x.key, r.sent, r.lost, r.rtts))
            self.latest[x.key] = summary(ts, r.sent, r.lost, r.rtts)
        await asyncio.to_thread(self.store.insert_samples, ts, rows)
        self.mtr.schedule(ts, series, results, s)
        await asyncio.to_thread(self.store.rollup, ts, s["raw_retention_days"] * 86400)
        self.last_run = ts
        self.last_duration = round(time.monotonic() - t0, 1)
        log.info("Round %s: %d series in %.1fs", time.strftime("%H:%M", time.localtime(ts)), len(rows), self.last_duration)


async def measure(series, s):
    by_probe = {}
    for x in series:
        by_probe.setdefault(x.target["probe"], []).append(x)
    names = [p for p in by_probe if p in PROBES]
    outs = await asyncio.gather(*[PROBES[p].run(by_probe[p], s) for p in names], return_exceptions=True)
    results = {}
    for p, out in zip(names, outs):
        if isinstance(out, Exception):
            log.error("Probe %s failed: %s", p, out)
            continue
        results.update(out)
    for x in series:
        if x.key not in results:
            cnt = PROBES[x.target["probe"]].count(x, s) if x.target["probe"] in PROBES else 1
            results[x.key] = Result(cnt, cnt, [])
    return results
