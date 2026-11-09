"""mtr traceroutes: every 15 minutes normally, every minute while a path has loss or is down.

mtr runs per network path (family + address), so a ping target and a DNS target pointing at
the same address share one traceroute history.
"""
import asyncio
import json
import logging
import shutil
import time

from . import rdns

log = logging.getLogger("pingscope")


def _num(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


async def run_mtr(family, address, count):
    cmd = ["mtr", "-n", "-j", "-c", str(count), f"-{family}", address]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=count * 3 + 30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("mtr timed out")
    if proc.returncode != 0 and not out:
        raise RuntimeError(err.decode(errors="replace").strip() or f"mtr exited with {proc.returncode}")
    hubs = json.loads(out)["report"]["hubs"]
    return [{
        "n": int(h.get("count", i + 1)), "host": h.get("host", "???"),
        "loss": _num(h.get("Loss%")), "snt": int(h.get("Snt", 0) or 0),
        "last": _num(h.get("Last")), "avg": _num(h.get("Avg")), "best": _num(h.get("Best")),
        "wrst": _num(h.get("Wrst")), "stdev": _num(h.get("StDev")),
    } for i, h in enumerate(hubs)]


class MtrScheduler:
    def __init__(self, store):
        self.store = store
        self.available = bool(shutil.which("mtr"))
        self.last = {}
        self.running = set()
        self.tasks = set()
        self.sem = None
        self.sem_size = 0
        if not self.available:
            log.warning("mtr not found; traceroutes are disabled (apt install mtr-tiny)")

    async def init(self):
        self.last = await asyncio.to_thread(self.store.last_mtr_times)

    async def _job(self, family, address, path, reason, s, trigger_loss=None):
        try:
            async with self.sem:
                ts = int(time.time())
                try:
                    hubs, error = await run_mtr(family, address, s["mtr_count"]), None
                except Exception as e:  # store failed runs too, so they show up in the history
                    hubs, error = [], (str(e)[:300] or e.__class__.__name__)
                    log.warning("mtr to %s failed: %s", address, error)
                if hubs and s.get("mtr_rdns", True):
                    try:  # reverse DNS names of the hops; a resolver problem never costs the traceroute itself
                        names = await rdns.lookup([h["host"] for h in hubs if h["host"] != "???"])
                        for h in hubs:
                            if names.get(h["host"]):
                                h["rdns"] = names[h["host"]]
                    except Exception:
                        log.exception("Reverse DNS for the traceroute to %s failed", address)
                await asyncio.to_thread(self.store.insert_mtr, path, ts, reason, hubs, error, round(trigger_loss, 1))
        except Exception:
            log.exception("Storing mtr result failed")
        finally:
            self.running.discard(path)

    def schedule(self, ts, series, results, s):
        """results only contains series that were actually measured (probe failures are left out)."""
        if not s["mtr_enabled"] or not self.available:
            return
        if self.sem is None or self.sem_size != s["mtr_max_parallel"]:
            self.sem = asyncio.Semaphore(s["mtr_max_parallel"])
            self.sem_size = s["mtr_max_parallel"]
        worst = {}
        for x in series:
            if not x.path or not x.target.get("mtr", True) or x.key not in results:
                continue
            r = results.get(x.key)
            loss = (r.lost / r.sent * 100) if r and r.sent else 100.0
            prev = worst.get(x.path)
            if prev is None or loss > prev[2]:
                worst[x.path] = (x.family, x.address, loss)
        for path, (family, address, loss) in worst.items():
            if path in self.running:
                continue
            if loss >= 100:
                reason = "down"
            elif loss > s["mtr_alert_loss_pct"]:
                reason = "loss"
            else:
                reason = "scheduled"
            due = s["mtr_interval"] if reason == "scheduled" else s["mtr_alert_interval"]
            if ts - self.last.get(path, 0) < due - 5:
                continue
            self.last[path] = ts
            self.running.add(path)
            task = asyncio.create_task(self._job(family, address, path, reason, s, loss))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        if ts % 3600 < s["interval"]:
            self.store.prune_mtr(ts - s["mtr_retention_days"] * 86400)
