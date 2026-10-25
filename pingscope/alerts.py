"""Notifications: per-group packet-loss and down alerts, emailed through Lettermint.

Every measurement round each series (target + IP family) is checked against the notification settings of its
group. A problem must last `alert_after_rounds` rounds before an alert starts, and the same number of good rounds
before it is resolved. All alerts that start, get worse or end in the same round go out in one email, so a large
outage gives one message instead of hundreds.
"""
import asyncio
import html
import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger("pingscope")

LETTERMINT_URL = os.environ.get("LETTERMINT_API_URL", "https://api.lettermint.co/v1/send")
MAX_LINES = 60  # targets listed per email; the rest is summarised


def token(s):
    return os.environ.get("LETTERMINT_TOKEN", "").strip() or s.get("lettermint_token", "")


def recipients(s):
    return [a.strip() for a in s.get("alert_email_to", "").split(",") if a.strip()]


def email_ready(s):
    """None when email can be sent, otherwise the reason it can't."""
    if not token(s):
        return "no Lettermint API token"
    if not s.get("alert_email_from"):
        return "no sender address"
    if not recipients(s):
        return "no recipient address"
    return None


def send_email(s, subject, text, body_html):
    """Send one email via the Lettermint API. Raises RuntimeError with a readable message on failure."""
    why = email_ready(s)
    if why:
        raise RuntimeError(f"Email is not set up: {why}")
    payload = {"from": s["alert_email_from"], "to": recipients(s), "subject": subject[:998],
               "text": text, "html": body_html, "tag": "pingscope-alert"}
    req = urllib.request.Request(LETTERMINT_URL, data=json.dumps(payload).encode(), method="POST", headers={
        "x-lettermint-token": token(s), "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "PingScope"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        try:
            j = json.loads(detail)
            detail = j.get("message") or j.get("error") or detail
        except ValueError:
            pass
        raise RuntimeError(f"Lettermint answered {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Could not reach Lettermint: {getattr(e, 'reason', e)}")


def base_url():
    d = os.environ.get("PINGSCOPE_DOMAIN", "").split(",")[0].strip()
    return f"https://{d}/" if d else ""


def fmt_dur(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{max(1, sec // 60)} min"
    if sec < 86400:
        return f"{sec // 3600} h {sec % 3600 // 60} min"
    return f"{sec // 86400} d {sec % 86400 // 3600} h"


def simulate(rounds, loss_pct, down, need):
    """Replay past rounds [(ts, sent, lost)] with the same rules as the live alerts.
    Returns [(start_ts, end_ts or None, kind, peak_loss)]."""
    out, bad, good, downs, worst, peak, cur = [], 0, 0, 0, None, 0, None
    for ts, sent, lost in rounds:
        if not sent:
            continue
        loss = lost / sent * 100
        kind = "down" if lost == sent and down else "loss" if loss_pct and loss >= loss_pct else None
        if kind:
            bad, good, peak = bad + 1, 0, max(peak, loss)
            downs = downs + 1 if kind == "down" else 0
            worst = "down" if downs >= need else "loss"
        else:
            downs, good, bad = 0, good + 1, 0
        if cur:
            cur[3] = max(cur[3], loss)
            if cur[2] == "loss" and downs >= need:
                cur[2] = "down"
            if good >= need:
                cur[1] = ts
                out.append(tuple(cur))
                cur, peak = None, 0
        elif kind and bad >= need:
            cur = [ts, None, worst, peak]
    if cur:
        out.append(tuple(cur))
    return out


class Alerter:
    def __init__(self, store):
        self.store = store
        self.track = {}        # series key -> {"bad": n, "good": n, "worst": kind, "peak": loss}
        self.open = {}         # series key -> alert row (dict)
        self.last_error = None
        self.last_sent = None
        self._loaded = False

    def _load(self):
        if not self._loaded:  # alerts that were open before a restart stay open: no duplicate emails
            self.open = {a["series"]: a for a in self.store.open_alerts()}
            self._loaded = True

    def check(self, ts, series, results, s):
        """Update alert state after a round. Returns the events to email: [(event, alert, series)]."""
        self._load()
        groups = {g["id"]: g for g in self.store.groups()}
        need = s["alert_after_rounds"]
        events = []
        live = set()
        for x in series:
            r = results.get(x.key)
            if r is None or not r.sent:  # probe could not run locally: no verdict this round
                live.add(x.key)
                continue
            live.add(x.key)
            cfg = (groups.get(x.target["group_id"]) or {}).get("alert_effective") or {}
            if x.target.get("alert_skip"):
                cfg = {}  # this target is excluded from alerts
            a = self.open.get(x.key)
            if not cfg.get("on"):
                if a:  # notifications were switched off: close quietly
                    self._close(a, ts)
                self.track.pop(x.key, None)
                continue
            loss = r.lost / r.sent * 100
            kind = None
            if r.lost == r.sent and cfg.get("down"):
                kind = "down"
            elif cfg.get("loss") and loss >= cfg["loss"]:
                kind = "loss"
            t = self.track.setdefault(x.key, {"bad": 0, "good": 0, "down": 0, "worst": None, "peak": 0})
            if kind:
                t["bad"] += 1
                t["good"] = 0
                t["peak"] = max(t["peak"], loss)
                t["down"] = t["down"] + 1 if kind == "down" else 0
                t["worst"] = "down" if t["down"] >= need else "loss"
            else:
                t["down"] = 0
                t["good"] += 1
                t["bad"] = 0
            if a:
                upd = {"last_loss": loss}
                if loss > (a["peak_loss"] or 0):
                    upd["peak_loss"] = loss
                if a["kind"] == "loss" and t["down"] >= need:
                    upd["kind"] = "down"
                    a.update(upd)
                    events.append(("down", a, x, cfg))
                elif t["good"] >= need:
                    a.update(upd)
                    self._close(a, ts)
                    events.append(("resolved", a, x, cfg))
                    t.update(worst=None, peak=0)
                elif s["alert_reminder_hours"] and kind and \
                        ts - (a.get("last_email") or a["started"]) >= s["alert_reminder_hours"] * 3600:
                    events.append(("reminder", a, x, cfg))
                a.update(upd)
                self.store.update_alert(a["id"], **{k: v for k, v in upd.items() if k != "ended"})
            elif kind and t["bad"] >= need:
                started = ts - (need - 1) * s["interval"]
                aid = self.store.create_alert(x.key, x.target_id, x.family, t["worst"], started, t["peak"])
                a = {"id": aid, "series": x.key, "target_id": x.target_id, "family": x.family, "kind": t["worst"],
                     "started": started, "ended": None, "peak_loss": t["peak"], "last_loss": loss,
                     "emailed": 0, "last_email": None}
                self.open[x.key] = a
                events.append(("start", a, x, cfg))
        for key, a in list(self.open.items()):  # target deleted or disabled
            if key not in live:
                self._close(a, ts)
        for key in list(self.track):
            if key not in live:
                del self.track[key]
        self.store.prune_alerts(ts - 90 * 86400)
        if not s["alert_recovery"]:
            events = [e for e in events if e[0] != "resolved"]
        return [e for e in events if e[3].get("email")]

    def _close(self, a, ts):
        a["ended"] = ts
        self.store.update_alert(a["id"], ended=ts)
        self.open.pop(a["series"], None)

    async def notify(self, ts, events, s):
        if not events:
            return
        why = email_ready(s)
        if why:
            self.last_error = f"{len(events)} alert email(s) not sent: {why}. Set it up in Admin → Settings → Notifications."
            log.warning(self.last_error)
            return
        subject, text, body = self.compose(ts, events, s)
        try:
            await asyncio.to_thread(send_email, s, subject, text, body)
        except RuntimeError as e:
            self.last_error = f"Alert email failed at {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}: {e}"
            log.error(self.last_error)
            return
        self.last_error, self.last_sent = None, ts
        for _, a, _, _ in events:
            self.store.update_alert(a["id"], emailed=1, last_email=ts)
            a["last_email"] = ts
        log.info("Alert email sent: %s", subject)

    def compose(self, ts, events, s):
        groups = {g["id"]: g for g in self.store.groups()}

        def path(gid):
            out = []
            while gid in groups:
                out.insert(0, groups[gid]["name"])
                gid = groups[gid]["parent_id"]
            return " › ".join(out)

        label = {"start": "Problem", "down": "Down", "reminder": "Still a problem", "resolved": "Resolved"}
        order = {"down": 0, "start": 1, "reminder": 2, "resolved": 3}
        events = sorted(events, key=lambda e: (order[e[0]], path(e[2].target["group_id"]), e[2].target["name"]))
        rows = []
        for ev, a, x, cfg in events:
            what = "down (no replies)" if a["kind"] == "down" else f"packet loss {a['last_loss']:.0f}%"
            if ev == "resolved":
                what = f"fine again after {fmt_dur(ts - a['started'])} (worst: {a['peak_loss']:.0f}% loss)"
            elif ev == "reminder":
                what += f", for {fmt_dur(ts - a['started'])}"
            elif a["kind"] == "loss":
                what += f" (threshold {cfg.get('loss')}%)"
            rows.append({"ev": ev, "status": label[ev], "name": x.target["name"], "group": path(x.target["group_id"]),
                         "fam": f"IPv{x.family}", "addr": x.address or x.target.get("host") or "", "what": what,
                         "link": f"{base_url()}#/t/{x.target_id}" if base_url() else ""})
        bad = [r for r in rows if r["ev"] != "resolved"]
        ok = [r for r in rows if r["ev"] == "resolved"]
        site = s.get("site_title") or "PingScope"
        if len(rows) == 1:
            r = rows[0]
            subject = f"[{site}] {r['status'].upper() if r['ev'] != 'resolved' else 'Resolved'}: {r['name']} ({r['fam']})"
        else:
            parts = []
            if bad:
                parts.append(f"{len(bad)} problem{'s' if len(bad) > 1 else ''}")
            if ok:
                parts.append(f"{len(ok)} resolved")
            subject = f"[{site}] {', '.join(parts)}"
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        shown, more = rows[:MAX_LINES], len(rows) - MAX_LINES
        text = f"{site} — {when}\n\n" + "\n".join(
            f"{r['status']}: {r['name']} ({r['fam']}, {r['addr']}) — {r['what']}\n  Group: {r['group']}"
            + (f"\n  {r['link']}" if r["link"] else "") for r in shown)
        if more > 0:
            text += f"\n\n… and {more} more."
        if base_url():
            text += f"\n\nDashboard: {base_url()}"
        color = {"start": "#d97706", "down": "#d62828", "reminder": "#d97706", "resolved": "#16a34a"}
        e = html.escape
        def name_cell(r):
            n = e(r["name"])
            if r["link"]:
                return f'<a href="{e(r["link"])}" style="color:#1f5fbf;font-weight:600;text-decoration:none">{n}</a>'
            return f"<b>{n}</b>"

        td = 'style="padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top"'
        trs = "".join(
            f'<tr><td {td}><span style="display:inline-block;padding:2px 8px;border-radius:4px;'
            f'background:{color[r["ev"]]};color:#fff;font-size:12px;font-weight:600;white-space:nowrap">{e(r["status"])}</span></td>'
            f'<td {td}>{name_cell(r)}<div style="color:#666;font-size:12px">{e(r["group"])} · {e(r["fam"])} · {e(r["addr"])}</div></td>'
            f'<td {td}>{e(r["what"])}</td></tr>' for r in shown)
        body = (f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;color:#1a1a1a">'
                f'<h2 style="margin:0 0 4px;font-size:18px">{e(site)}</h2><div style="color:#666;margin-bottom:14px">{e(when)}</div>'
                f'<table style="border-collapse:collapse;width:100%;max-width:760px">{trs}</table>'
                + (f'<p style="color:#666">… and {more} more.</p>' if more > 0 else "")
                + (f'<p><a href="{e(base_url())}" style="color:#1f5fbf">Open the dashboard</a></p>' if base_url() else "")
                + '<p style="color:#999;font-size:12px">Sent by PingScope. Notification settings are per group in Admin → Targets.</p></div>')
        return subject, text, body
