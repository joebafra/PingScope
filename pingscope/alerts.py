"""Notifications: per-group packet-loss and down alerts, sent by email (Lettermint) and/or Telegram.

Every measurement round each series (target + IP family) is checked against the notification settings of its
group. A problem must last `alert_after_rounds` rounds before an alert starts, and the same number of good rounds
before it is resolved. All alerts that start, get worse or end in the same round go out in one message, so a
large outage gives one message instead of hundreds. Targets can have their own extra Telegram chat IDs (e.g. the
customer of that connection); those chats only get messages about that target.
"""
import asyncio
import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger("pingscope")

LETTERMINT_URL = os.environ.get("LETTERMINT_API_URL", "https://api.lettermint.co/v1/send")
TELEGRAM_API = os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org")
MAX_LINES = 60  # targets listed per message; the rest is summarised


def chat_list(v):
    return [x for x in re.split(r"[\s,;]+", str(v or "").strip()) if x]


def with_contacts(s, store):
    """Settings where telegram_chat_ids also includes the saved contacts marked "gets all alerts"."""
    ids = chat_list(s.get("telegram_chat_ids")) + [c["chat_id"] for c in store.contacts() if c["is_default"]]
    return dict(s, telegram_chat_ids=", ".join(dict.fromkeys(ids)))


def tg_token(s):
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or s.get("telegram_bot_token", "")


def telegram_ready(s, chats=None):
    if not tg_token(s):
        return "no Telegram bot token"
    if chats is None and not chat_list(s.get("telegram_chat_ids")):
        return "no Telegram chat ID"
    return None


def _tg_call(s, method, payload=None):
    req = urllib.request.Request(f"{TELEGRAM_API}/bot{tg_token(s)}/{method}",
                                 data=json.dumps(payload or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "PingScope"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}").get("result")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("description") or ""
        except ValueError:
            detail = ""
        if e.code == 401 or e.code == 404:
            detail = "the bot token is wrong"
        raise RuntimeError(f"Telegram answered {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Could not reach Telegram: {getattr(e, 'reason', e)}")


def send_telegram(s, chat_id, text_html):
    """Send a message (Telegram HTML) to one chat, split into parts when it is too long."""
    why = telegram_ready(s, chats=[chat_id])
    if why:
        raise RuntimeError(f"Telegram is not set up: {why}")
    parts, cur = [], ""
    for line in text_html.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    parts.append(cur)
    for part in parts:
        if part.strip():
            try:
                _tg_call(s, "sendMessage", {"chat_id": chat_id, "text": part, "parse_mode": "HTML",
                                            "disable_web_page_preview": True})
            except RuntimeError as e:
                raise RuntimeError(f"chat {chat_id}: {e}")


def telegram_chats(s):
    """Chats that recently wrote to the bot (send /start to it first): [{id, name, type}]."""
    if not tg_token(s):
        raise RuntimeError("Enter the Telegram bot token in Settings → Notifications first")
    out = {}
    for u in _tg_call(s, "getUpdates", {"limit": 100}) or []:
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = m.get("chat")
        if c:
            name = c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
            if c.get("username"):
                name += f" (@{c['username']})"
            out[c["id"]] = {"id": c["id"], "name": name.strip(), "type": c.get("type")}
    return list(out.values())


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
        return [e for e in events if e[3].get("email") or e[3].get("telegram") or chat_list(e[2].target.get("alert_chat_ids"))]

    def _close(self, a, ts):
        a["ended"] = ts
        self.store.update_alert(a["id"], ended=ts)
        self.open.pop(a["series"], None)

    async def notify(self, ts, events, s):
        if not events:
            return
        s = with_contacts(s, self.store)
        errors, sent_any = [], False
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        mail_used = bool(token(s) or s.get("alert_email_from") or recipients(s))
        tg_used = bool(tg_token(s) or chat_list(s.get("telegram_chat_ids")))
        mail = [e for e in events if e[3].get("email")] if mail_used else []
        tg = [e for e in events if e[3].get("telegram")] if tg_used else []
        unsent = [e for e in events if (e[3].get("email") or e[3].get("telegram")) and e not in mail and e not in tg]
        if unsent and not mail and not tg:
            errors.append(f"{len(unsent)} alert(s) not sent: set up Telegram or email in Settings → Notifications")
        if mail:
            why = email_ready(s)
            if why:
                errors.append(f"{len(mail)} alert(s) not emailed: {why}")
            else:
                subject, text, body = self.compose(ts, mail, s)
                try:
                    await asyncio.to_thread(send_email, s, subject, text, body)
                    sent_any = True
                    log.info("Alert email sent: %s", subject)
                except RuntimeError as e:
                    errors.append(f"Alert email failed at {when}: {e}")
        if tg:
            why = telegram_ready(s)
            if why:
                errors.append(f"{len(tg)} alert(s) not sent to Telegram: {why}")
            else:
                msg = self.compose_telegram(ts, tg, s)
                for chat in chat_list(s["telegram_chat_ids"]):
                    try:
                        await asyncio.to_thread(send_telegram, s, chat, msg)
                        sent_any = True
                    except RuntimeError as e:
                        errors.append(f"Telegram alert failed at {when}: {e}")
        # extra chats per target: only messages about their own target(s)
        per_chat = {}
        for e in events:
            for chat in chat_list(e[2].target.get("alert_chat_ids")):
                per_chat.setdefault(chat, []).append(e)
        for chat, evs in per_chat.items():
            if not tg_token(s):
                errors.append("Targets have their own Telegram chat IDs, but no Telegram bot token is set")
                break
            try:
                await asyncio.to_thread(send_telegram, s, chat, self.compose_telegram(ts, evs, s, external=True))
                sent_any = True
            except RuntimeError as e:
                errors.append(f"Telegram alert failed at {when}: {e}")
        self.last_error = "; ".join(dict.fromkeys(errors)) or None
        if errors:
            log.error(self.last_error)
        if sent_any:
            self.last_sent = ts
            for _, a, _, _ in events:
                self.store.update_alert(a["id"], emailed=1, last_email=ts)
                a["last_email"] = ts

    async def test_alert(self, s, x, chats=None):
        """Send a sample alert for series x, marked as a test. chats=None: the admin channels (Telegram defaults
        and email); otherwise only those Telegram chats, formatted like a customer's message.
        Returns {"sent": [...], "errors": [...]}."""
        s = with_contacts(s, self.store)
        ts = int(time.time())
        a = {"kind": "loss", "last_loss": 25.0, "peak_loss": 25.0, "started": ts - 3 * s["interval"]}
        ev = [("start", a, x, {"loss": 10})]
        sent, errors = [], []
        if chats is None:
            chats = chat_list(s["telegram_chat_ids"]) if tg_token(s) else []
            external = False
            if email_ready(s) is None:
                subject, text, body = self.compose(ts, ev, s)
                try:
                    await asyncio.to_thread(send_email, s, "[TEST] " + subject, "TEST — this is not a real alert.\n\n" + text,
                                            '<p style="font-family:sans-serif;color:#d97706"><b>TEST</b> — this is not a real alert.</p>' + body)
                    sent.append("email to " + ", ".join(recipients(s)))
                except RuntimeError as e:
                    errors.append(str(e))
        else:
            external = True
        msg = "🧪 <b>TEST</b> — this is not a real alert.\n\n" + self.compose_telegram(ts, ev, s, external=external)
        for chat in chats:
            try:
                await asyncio.to_thread(send_telegram, s, chat, msg)
                sent.append(f"Telegram {chat}")
            except RuntimeError as e:
                errors.append(str(e))
        if not sent and not errors:
            errors.append("Nothing to send to: set up Telegram (bot token + chat ID) or email in Settings → Notifications")
        return {"sent": sent, "errors": errors}

    def _rows(self, ts, events):
        groups = {g["id"]: g for g in self.store.groups()}

        def path(gid):
            out = []
            while gid in groups:
                out.insert(0, groups[gid]["name"])
                gid = groups[gid]["parent_id"]
            return " › ".join(out)

        public = self.store.settings()["public_dashboard"]
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
            elif a["kind"] == "loss" and cfg.get("loss"):
                what += f" (threshold {cfg.get('loss')}%)"
            g = groups.get(x.target["group_id"]) or {}
            if ev == "start" and a["kind"] == "down":
                ev = "down"
            rows.append({"ev": ev, "status": label[ev], "name": x.target["name"], "group": path(x.target["group_id"]),
                         "fam": f"IPv{x.family}", "addr": x.address or x.target.get("host") or "", "what": what,
                         "link": f"{base_url()}#/t/{x.target_id}" if base_url() else "",
                         "public_link": public and not g.get("hidden_effective")})
        return rows

    def compose_telegram(self, ts, events, s, external=False):
        """Telegram HTML message. external=True (a target's own chat): no group names, only public links."""
        rows = self._rows(ts, events)
        e = html.escape
        icon = {"start": "🟠", "down": "🔴", "reminder": "🟠", "resolved": "🟢"}
        site = s.get("site_title") or "PingScope"
        lines = [f"<b>{e(site)}</b> · {time.strftime('%d-%m %H:%M', time.localtime(ts))}", ""]
        for r in rows[:MAX_LINES]:
            link = r["link"] if r["link"] and (not external or r["public_link"]) else ""
            name = f'<a href="{e(link)}">{e(r["name"])}</a>' if link else f"<b>{e(r['name'])}</b>"
            lines.append(f"{icon[r['ev']]} <b>{e(r['status'])}</b>: {name} ({e(r['fam'])})")
            lines.append(f"    {e(r['what'])}")
            detail = r["addr"] if external else f"{r['group']} · {r['addr']}"
            if detail:
                lines.append(f"    <i>{e(detail)}</i>")
        if len(rows) > MAX_LINES:
            lines.append(f"\n… and {len(rows) - MAX_LINES} more.")
        return "\n".join(lines)

    def compose(self, ts, events, s):
        rows = self._rows(ts, events)
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
