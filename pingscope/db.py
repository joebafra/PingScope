"""SQLite storage: configuration (groups, targets, settings, users) and measurements."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from array import array

from . import settings as settings_mod

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS targets(
  id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL, probe TEXT NOT NULL DEFAULT 'ping',
  host TEXT NOT NULL DEFAULT '', host_v6 TEXT NOT NULL DEFAULT '', families TEXT NOT NULL DEFAULT 'auto',
  dns_query TEXT NOT NULL DEFAULT '', dns_type TEXT NOT NULL DEFAULT 'A',
  port INTEGER, count INTEGER, mtr INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
  note TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL DEFAULT 0,
  alert_skip INTEGER NOT NULL DEFAULT 0, alert_chat_ids TEXT NOT NULL DEFAULT '', alert_rounds INTEGER,
  hide_address INTEGER NOT NULL DEFAULT 0, spacing_ms INTEGER, timeout_ms INTEGER, packet_size INTEGER,
  start_offset TEXT NOT NULL DEFAULT '', measure_every INTEGER);
CREATE INDEX IF NOT EXISTS targets_group ON targets(group_id);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS samples(
  series TEXT NOT NULL, ts INTEGER NOT NULL, sent INTEGER NOT NULL, lost INTEGER NOT NULL,
  rtts BLOB NOT NULL, PRIMARY KEY(series, ts)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS hourly(
  series TEXT NOT NULL, ts INTEGER NOT NULL, sent INTEGER NOT NULL, lost INTEGER NOT NULL,
  q BLOB NOT NULL, PRIMARY KEY(series, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS mtr(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, ts INTEGER NOT NULL, reason TEXT NOT NULL,
  loss REAL, hops INTEGER, hubs TEXT NOT NULL, error TEXT);
CREATE INDEX IF NOT EXISTS mtr_path_ts ON mtr(path, ts);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, pw_hash TEXT NOT NULL, created INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin');
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, series TEXT NOT NULL, target_id INTEGER NOT NULL, family INTEGER NOT NULL,
  kind TEXT NOT NULL, started INTEGER NOT NULL, ended INTEGER, peak_loss REAL, last_loss REAL,
  emailed INTEGER NOT NULL DEFAULT 0, last_email INTEGER);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts(ended);
CREATE INDEX IF NOT EXISTS alerts_started ON alerts(started);
CREATE TABLE IF NOT EXISTS telegram_contacts(
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL UNIQUE, label TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0);
"""

# Notifications per group. mode: inherit (same as the parent group; top level = off), off or on.
ALERT_MODES = ("inherit", "off", "on")

TARGET_FIELDS = ("name", "probe", "host", "host_v6", "families", "dns_query", "dns_type",
                 "port", "count", "mtr", "enabled", "note", "alert_skip", "alert_chat_ids", "alert_rounds",
                 "hide_address", "spacing_ms", "timeout_ms", "packet_size", "start_offset", "measure_every")


# ---- helpers -------------------------------------------------------------------

def pack(values):
    return array("f", values).tobytes()


def unpack(blob):
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


def quantiles(values, n=20):
    """Up to n evenly spaced quantiles of the values (sorted)."""
    if not values:
        return []
    s = sorted(values)
    if len(s) <= n:
        return s
    out = []
    for i in range(n):
        p = i / (n - 1) * (len(s) - 1)
        lo = int(p)
        hi = min(lo + 1, len(s) - 1)
        f = p - lo
        out.append(s[lo] * (1 - f) + s[hi] * f)
    return out


def median(q):
    if not q:
        return None
    k = len(q)
    return q[k // 2] if k % 2 else (q[k // 2 - 1] + q[k // 2]) / 2


def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw, stored):
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


GROUP_COLS = ("id, parent_id, name, sort, sort_mode, hidden, show_addresses, measure_every, alert_mode, alert_loss, alert_down, alert_email, "
              "alert_telegram, alert_pushover, alert_rounds, alert_chat_ids")

# admin: full access. viewer: sees the dashboard like an admin (hostnames, hidden groups), changes nothing.
ROLES = ("admin", "viewer")

class Store:
    def __init__(self, path):
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.executescript(SCHEMA)
        if "trigger_loss" not in [r[1] for r in self.c.execute("PRAGMA table_info(mtr)")]:
            self.c.execute("ALTER TABLE mtr ADD COLUMN trigger_loss REAL")
        if "sort_mode" not in [r[1] for r in self.c.execute("PRAGMA table_info(groups)")]:
            self.c.execute("ALTER TABLE groups ADD COLUMN sort_mode TEXT NOT NULL DEFAULT 'manual'")
        if "hidden" not in [r[1] for r in self.c.execute("PRAGMA table_info(groups)")]:
            self.c.execute("ALTER TABLE groups ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        if "role" not in [r[1] for r in self.c.execute("PRAGMA table_info(users)")]:
            self.c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")  # existing users stay admin
        tcols = [r[1] for r in self.c.execute("PRAGMA table_info(targets)")]
        if "alert_skip" not in tcols:
            self.c.execute("ALTER TABLE targets ADD COLUMN alert_skip INTEGER NOT NULL DEFAULT 0")
        if "alert_chat_ids" not in tcols:
            self.c.execute("ALTER TABLE targets ADD COLUMN alert_chat_ids TEXT NOT NULL DEFAULT ''")
        if "alert_rounds" not in tcols:  # NULL = use the group's setting
            self.c.execute("ALTER TABLE targets ADD COLUMN alert_rounds INTEGER")
        if "hide_address" not in tcols:  # 1 = never show this target's addresses to visitors
            self.c.execute("ALTER TABLE targets ADD COLUMN hide_address INTEGER NOT NULL DEFAULT 0")
        for col, ddl in (("spacing_ms", "INTEGER"), ("timeout_ms", "INTEGER"), ("packet_size", "INTEGER"),
                         ("start_offset", "TEXT NOT NULL DEFAULT ''"),
                         ("measure_every", "INTEGER")):  # per-target probe settings; NULL/'' = group or global
            if col not in tcols:
                self.c.execute(f"ALTER TABLE targets ADD COLUMN {col} {ddl}")
        gcols = [r[1] for r in self.c.execute("PRAGMA table_info(groups)")]
        for col, ddl in (("alert_mode", "TEXT NOT NULL DEFAULT 'inherit'"), ("alert_loss", "INTEGER"),
                         ("alert_down", "INTEGER NOT NULL DEFAULT 1"), ("alert_email", "INTEGER NOT NULL DEFAULT 1"),
                         ("alert_telegram", "INTEGER NOT NULL DEFAULT 1"), ("alert_pushover", "INTEGER NOT NULL DEFAULT 1"),
                         ("alert_rounds", "INTEGER"),
                         ("alert_chat_ids", "TEXT NOT NULL DEFAULT ''"),
                         ("show_addresses", "INTEGER NOT NULL DEFAULT 0"),  # 1 = visitors see hosts and addresses
                         ("measure_every", "INTEGER")):  # measure every N rounds; NULL = same as the parent group
            if col not in gcols:
                self.c.execute(f"ALTER TABLE groups ADD COLUMN {col} {ddl}")
        self.lock = threading.RLock()
        self._settings = None

    def _q(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchone()

    # ---- meta / settings ---------------------------------------------------------

    def meta(self, k, default=None):
        r = self._one("SELECT v FROM meta WHERE k=?", (k,))
        return r[0] if r else default

    def set_meta_if_missing(self, k, v):
        with self.lock, self.c:
            self.c.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (k, v))

    def set_meta(self, k, v):
        with self.lock, self.c:
            self.c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))

    def settings(self):
        if self._settings is None:
            s = dict(settings_mod.DEFAULTS)
            for k, v in self._q("SELECT k, v FROM settings"):
                if k in s:
                    try:
                        s[k] = settings_mod.coerce(k, json.loads(v))
                    except ValueError:
                        pass
            self._settings = s
        return dict(self._settings)

    def save_settings(self, values):
        clean = {k: settings_mod.coerce(k, v) for k, v in values.items()}
        with self.lock, self.c:
            self.c.executemany("INSERT OR REPLACE INTO settings VALUES (?,?)",
                               [(k, json.dumps(v)) for k, v in clean.items()])
            self._settings = None
        return self.settings()

    # ---- groups --------------------------------------------------------------------

    def groups(self):
        out = [dict(r) for r in self._q(f"SELECT {GROUP_COLS} FROM groups ORDER BY sort, id")]
        by = {g["id"]: g for g in out}
        for g in out:
            g["alert_down"], g["alert_email"] = bool(g["alert_down"]), bool(g["alert_email"])
            g["alert_telegram"] = bool(g["alert_telegram"])
            g["alert_pushover"] = bool(g["alert_pushover"])
        for g in out:  # effective notification settings: the first group up the tree that isn't "inherit"
            p, src = g, None
            while p is not None:
                if p["alert_mode"] != "inherit":
                    src = p
                    break
                p = by.get(p["parent_id"])
            on = bool(src and src["alert_mode"] == "on")
            g["alert_effective"] = {"on": on, "from": src["id"] if src else None,
                                    "loss": src["alert_loss"] if on else None, "down": on and src["alert_down"],
                                    "email": on and src["alert_email"], "telegram": on and src["alert_telegram"],
                                    "pushover": on and src["alert_pushover"],
                                    "rounds": src["alert_rounds"] if on else None}  # None = the global setting
        for g in out:  # extra Telegram chats: this group's and all parent groups' together
            chats, p = [], g
            while p is not None:
                chats = [x for x in re.split(r"[\s,;]+", p["alert_chat_ids"]) if x] + chats
                p = by.get(p["parent_id"])
            g["alert_effective"]["chats"] = list(dict.fromkeys(chats))
        for g in out:  # effective visibility: a group is hidden when it or any parent group is hidden
            g["hidden"] = bool(g["hidden"])
            p, hid = g["parent_id"], g["hidden"]
            while p in by and not hid:
                hid, p = bool(by[p]["hidden"]), by[p]["parent_id"]
            g["hidden_effective"] = hid
        for g in out:  # addresses are shown to visitors when this group or any parent group shows them
            g["show_addresses"] = bool(g["show_addresses"])
            p, show = g, False
            while p is not None and not show:
                show, p = bool(p["show_addresses"]), by.get(p["parent_id"])
            g["show_addresses_effective"] = show
        for g in out:  # measure every N rounds: the first group up the tree that sets it, else every round
            p = g
            while p is not None and not p["measure_every"]:
                p = by.get(p["parent_id"])
            g["measure_every_effective"] = p["measure_every"] if p is not None else 1
        return out

    def hidden_group_ids(self):
        return {g["id"] for g in self.groups() if g["hidden_effective"]}

    def set_hidden(self, gid, hidden):
        with self.lock, self.c:
            self.c.execute("UPDATE groups SET hidden=? WHERE id=?", (1 if hidden else 0, gid))

    def set_measure_every(self, gid, n):
        """Measure the group's targets every n rounds; None = same as the parent group."""
        n = _every(n)
        with self.lock, self.c:
            self.c.execute("UPDATE groups SET measure_every=? WHERE id=?", (n, gid))

    def set_show_addresses(self, gid, show):
        with self.lock, self.c:
            self.c.execute("UPDATE groups SET show_addresses=? WHERE id=?", (1 if show else 0, gid))

    def group(self, gid):
        r = self._one(f"SELECT {GROUP_COLS} FROM groups WHERE id=?", (gid,))
        return dict(r) if r else None

    def _next_sort(self, table, col, val):
        if val is None:
            r = self._one(f"SELECT COALESCE(MAX(sort), -1) + 1 FROM {table} WHERE {col} IS NULL")
        else:
            r = self._one(f"SELECT COALESCE(MAX(sort), -1) + 1 FROM {table} WHERE {col}=?", (val,))
        return r[0]

    def descendants(self, gid):
        out, todo = set(), [gid]
        rows = self._q("SELECT id, parent_id FROM groups")
        children = {}
        for r in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])
        while todo:
            g = todo.pop()
            for c in children.get(g, []):
                if c not in out:
                    out.add(c)
                    todo.append(c)
        return out

    def create_group(self, name, parent_id=None, sort_mode="manual", hidden=False, show_addresses=False):
        name = str(name or "").strip()[:200]
        if not name:
            raise ValueError("Group name is required")
        if parent_id is not None and not self.group(parent_id):
            raise ValueError("Parent group not found")
        sort_mode = _sort_mode(sort_mode)
        with self.lock, self.c:
            cur = self.c.execute("INSERT INTO groups(parent_id, name, sort, sort_mode, hidden, show_addresses) VALUES (?,?,?,?,?,?)",
                                 (parent_id, name, self._next_sort("groups", "parent_id", parent_id), sort_mode,
                                  1 if hidden else 0, 1 if show_addresses else 0))
            return cur.lastrowid

    def set_alerts(self, gid, mode, loss=None, down=True, email=True, telegram=True, rounds=None, pushover=True):
        rounds = _rounds(rounds)
        if mode not in ALERT_MODES:
            raise ValueError("Notifications must be inherit, off or on")
        if loss in ("", None):
            loss = None
        else:
            try:
                loss = int(loss)
            except (TypeError, ValueError):
                raise ValueError("Packet loss threshold must be a whole number")
            if not 1 <= loss <= 100:
                raise ValueError("Packet loss threshold must be between 1 and 100%")
        if mode == "on" and loss is None and not down:
            raise ValueError("Choose a packet loss threshold, alerts when down, or both")
        with self.lock, self.c:
            self.c.execute("UPDATE groups SET alert_mode=?, alert_loss=?, alert_down=?, alert_email=?, alert_telegram=?, "
                           "alert_pushover=?, alert_rounds=? WHERE id=?",
                           (mode, loss, 1 if down else 0, 1 if email else 0, 1 if telegram else 0, 1 if pushover else 0,
                            rounds, gid))

    def set_group_chats(self, gid, value):
        ids = [x for x in re.split(r"[\s,;]+", str(value or "").strip()) if x]
        bad = [x for x in ids if not re.fullmatch(r"-?\d{3,20}|@[A-Za-z]\w{4,31}", x)]
        if bad:
            raise ValueError(f"Not a Telegram chat ID: {', '.join(bad[:3])}")
        with self.lock, self.c:
            self.c.execute("UPDATE groups SET alert_chat_ids=? WHERE id=?", (", ".join(dict.fromkeys(ids))[:500], gid))

    def set_sort_mode(self, gid, sort_mode, include_subgroups=False):
        ids = [gid, *(self.descendants(gid) if include_subgroups else [])]
        with self.lock, self.c:
            self.c.executemany("UPDATE groups SET sort_mode=? WHERE id=?", [(_sort_mode(sort_mode), i) for i in ids])

    def update_group(self, gid, name=None, parent_id="keep"):
        g = self.group(gid)
        if not g:
            raise KeyError("Group not found")
        if name is not None:
            name = str(name).strip()[:200]
            if not name:
                raise ValueError("Group name is required")
        with self.lock, self.c:
            if name is not None:
                self.c.execute("UPDATE groups SET name=? WHERE id=?", (name, gid))
            if parent_id != "keep" and parent_id != g["parent_id"]:
                if parent_id is not None:
                    if parent_id == gid or parent_id in self.descendants(gid):
                        raise ValueError("A group can't be moved into itself or one of its subgroups")
                    if not self.group(parent_id):
                        raise ValueError("Parent group not found")
                self.c.execute("UPDATE groups SET parent_id=?, sort=? WHERE id=?",
                               (parent_id, self._next_sort("groups", "parent_id", parent_id), gid))

    def delete_group(self, gid):
        ids = [gid, *self.descendants(gid)]
        tids = [r[0] for r in self._q(
            f"SELECT id FROM targets WHERE group_id IN ({','.join('?' * len(ids))})", ids)]
        with self.lock, self.c:
            self.c.execute("DELETE FROM groups WHERE id=?", (gid,))
            self._purge_series(tids)
        return len(tids)

    # ---- targets ---------------------------------------------------------------------

    @staticmethod
    def _target(r):
        t = dict(r)
        t["mtr"] = bool(t["mtr"])
        t["enabled"] = bool(t["enabled"])
        t["alert_skip"] = bool(t["alert_skip"])
        t["hide_address"] = bool(t.get("hide_address"))
        return t

    def targets(self, enabled_only=False):
        sql = "SELECT * FROM targets" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY sort, id"
        return [self._target(r) for r in self._q(sql)]

    def target(self, tid):
        r = self._one("SELECT * FROM targets WHERE id=?", (tid,))
        return self._target(r) if r else None

    def create_target(self, group_id, t):
        if not self.group(group_id):
            raise ValueError("Group not found")
        cols = ", ".join(TARGET_FIELDS)
        with self.lock, self.c:
            cur = self.c.execute(
                f"INSERT INTO targets(group_id, {cols}, sort, created) VALUES (?, {', '.join('?' * len(TARGET_FIELDS))}, ?, ?)",
                (group_id, *[t[k] for k in TARGET_FIELDS], self._next_sort("targets", "group_id", group_id), int(time.time())),
            )
            return cur.lastrowid

    def update_target(self, tid, t, group_id=None):
        old = self.target(tid)
        if not old:
            raise KeyError("Target not found")
        with self.lock, self.c:
            self.c.execute(f"UPDATE targets SET {', '.join(k + '=?' for k in TARGET_FIELDS)} WHERE id=?",
                           (*[t[k] for k in TARGET_FIELDS], tid))
            if group_id is not None and group_id != old["group_id"]:
                if not self.group(group_id):
                    raise ValueError("Group not found")
                self.c.execute("UPDATE targets SET group_id=?, sort=? WHERE id=?",
                               (group_id, self._next_sort("targets", "group_id", group_id), tid))

    def set_targets(self, ids, **fields):
        allowed = {"enabled", "mtr", "group_id", "alert_skip"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not ids or not sets:
            return
        with self.lock, self.c:
            for tid in ids:
                self.c.execute(f"UPDATE targets SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), tid))

    def delete_targets(self, ids):
        with self.lock, self.c:
            self.c.executemany("DELETE FROM targets WHERE id=?", [(i,) for i in ids])
            self._purge_series(ids)

    def _purge_series(self, tids):
        for tid in tids:
            for fam in (4, 6):
                k = f"{tid}-{fam}"
                self.c.execute("DELETE FROM samples WHERE series=?", (k,))
                self.c.execute("DELETE FROM hourly WHERE series=?", (k,))
            self.c.execute("DELETE FROM alerts WHERE target_id=?", (tid,))

    def reorder(self, kind, ids):
        table = {"group": "groups", "target": "targets"}[kind]
        with self.lock, self.c:
            self.c.executemany(f"UPDATE {table} SET sort=? WHERE id=?", [(i, x) for i, x in enumerate(ids)])

    def clear_config(self):
        with self.lock, self.c:
            self.c.execute("DELETE FROM targets")
            self.c.execute("DELETE FROM groups")
            self.c.execute("DELETE FROM samples")
            self.c.execute("DELETE FROM hourly")
            self.c.execute("DELETE FROM alerts")
            self.c.execute("DELETE FROM meta WHERE k='rolled_until'")

    # ---- users & sessions -----------------------------------------------------------

    def users(self):
        return [dict(r) for r in self._q("SELECT id, username, created, role FROM users ORDER BY username")]

    def admin_count(self):
        return self._one("SELECT COUNT(*) FROM users WHERE role='admin'")[0]

    def user_count(self):
        return self._one("SELECT COUNT(*) FROM users")[0]

    def create_user(self, username, password, role="admin"):
        username = str(username or "").strip()
        if role not in ROLES:
            raise ValueError("Role must be admin or viewer")
        if not username or len(username) > 64:
            raise ValueError("Username is required (max 64 characters)")
        if len(password or "") < 10:
            raise ValueError("Password must be at least 10 characters")
        try:
            with self.lock, self.c:
                cur = self.c.execute("INSERT INTO users(username, pw_hash, created, role) VALUES (?,?,?,?)",
                                     (username, hash_password(password), int(time.time()), role))
                return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("That username already exists")

    def set_password(self, uid, password):
        if len(password or "") < 10:
            raise ValueError("Password must be at least 10 characters")
        with self.lock, self.c:
            self.c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(password), uid))
            self.c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))

    def set_role(self, uid, role):
        if role not in ROLES:
            raise ValueError("Role must be admin or viewer")
        with self.lock, self.c:
            self.c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))

    def delete_user(self, uid):
        with self.lock, self.c:
            self.c.execute("DELETE FROM users WHERE id=?", (uid,))

    def check_login(self, username, password):
        r = self._one("SELECT id, username, pw_hash, role FROM users WHERE username=?", (str(username or "").strip(),))
        if r and verify_password(password or "", r["pw_hash"]):
            return {"id": r["id"], "username": r["username"], "role": r["role"]}
        if not r:
            verify_password("x", hash_password("timing"))  # even out timing
        return None

    @staticmethod
    def _tok(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, uid, token, ttl):
        with self.lock, self.c:
            self.c.execute("DELETE FROM sessions WHERE expires<?", (int(time.time()),))
            self.c.execute("INSERT INTO sessions VALUES (?,?,?)", (self._tok(token), uid, int(time.time()) + ttl))

    def session_user(self, token):
        if not token:
            return None
        r = self._one("SELECT u.id, u.username, u.role FROM sessions s JOIN users u ON u.id=s.user_id "
                      "WHERE s.token=? AND s.expires>?", (self._tok(token), int(time.time())))
        return dict(r) if r else None

    def delete_session(self, token):
        with self.lock, self.c:
            self.c.execute("DELETE FROM sessions WHERE token=?", (self._tok(token),))

    # ---- alerts ---------------------------------------------------------------------

    # ---- Telegram contacts (saved, labelled chat IDs) -----------------------------

    def contacts(self):
        out = [dict(r) for r in self._q("SELECT id, chat_id, label, is_default FROM telegram_contacts ORDER BY label COLLATE NOCASE")]
        for c in out:
            c["is_default"] = bool(c["is_default"])
        return out

    def save_contact(self, cid, chat_id, label, is_default):
        chat_id, label = str(chat_id or "").strip(), str(label or "").strip()[:100]
        if not re.fullmatch(r"-?\d{3,20}|@[A-Za-z]\w{4,31}", chat_id):
            raise ValueError("Not a Telegram chat ID (use numbers like 123456789 or -100123…)")
        if not label:
            raise ValueError("Give the contact a name")
        try:
            with self.lock, self.c:
                if cid:
                    old = self._one("SELECT chat_id FROM telegram_contacts WHERE id=?", (cid,))
                    if not old:
                        raise KeyError("Contact not found")
                    self.c.execute("UPDATE telegram_contacts SET chat_id=?, label=?, is_default=? WHERE id=?",
                                   (chat_id, label, 1 if is_default else 0, cid))
                    if old[0] != chat_id:  # keep targets and groups pointing at this contact
                        self._swap_chat(old[0], chat_id)
                    return cid
                return self.c.execute("INSERT INTO telegram_contacts(chat_id, label, is_default) VALUES (?,?,?)",
                                      (chat_id, label, 1 if is_default else 0)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("That chat ID is already saved")

    def delete_contact(self, cid):
        r = self._one("SELECT chat_id FROM telegram_contacts WHERE id=?", (cid,))
        if not r:
            return 0
        with self.lock, self.c:
            self.c.execute("DELETE FROM telegram_contacts WHERE id=?", (cid,))
            return self._swap_chat(r[0], None)

    def _swap_chat(self, old, new):
        """Replace (or with new=None remove) a chat ID on all targets and groups. Returns how many changed."""
        n = 0
        for table in ("targets", "groups"):
            for row in self._q(f"SELECT id, alert_chat_ids FROM {table} WHERE alert_chat_ids != ''"):
                ids = [x for x in re.split(r"[\s,;]+", row[1]) if x]
                if old in ids:
                    n += 1
                    ids = [new if x == old else x for x in ids if new or x != old]
                    self.c.execute(f"UPDATE {table} SET alert_chat_ids=? WHERE id=?", (", ".join(dict.fromkeys(ids)), row[0]))
        return n

    def open_alerts(self):
        return [dict(r) for r in self._q("SELECT * FROM alerts WHERE ended IS NULL")]

    def create_alert(self, series, target_id, family, kind, started, loss):
        with self.lock, self.c:
            return self.c.execute("INSERT INTO alerts(series, target_id, family, kind, started, peak_loss, last_loss) "
                                  "VALUES (?,?,?,?,?,?,?)", (series, target_id, family, kind, started, loss, loss)).lastrowid

    def update_alert(self, aid, **f):
        if f:
            with self.lock, self.c:
                self.c.execute(f"UPDATE alerts SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), aid))

    def alerts_since(self, since, limit=1000):
        """Alerts still open or ended after `since`, newest first."""
        return [dict(r) for r in self._q("SELECT * FROM alerts WHERE ended IS NULL OR ended>=? "
                                         "ORDER BY started DESC LIMIT ?", (since, limit))]

    def alert_list(self, limit=200):
        return [dict(r) for r in self._q("SELECT * FROM alerts ORDER BY ended IS NOT NULL, started DESC LIMIT ?", (limit,))]

    def loss_rounds(self, keys, since):
        """{series: [(ts, sent, lost), ...]} from the per-round data, oldest first."""
        out = {k: [] for k in keys}
        keys = list(keys)
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for r in self._q(f"SELECT series, ts, sent, lost FROM samples WHERE ts>=? AND series IN "
                             f"({','.join('?' * len(part))}) ORDER BY ts", (since, *part)):
                out[r[0]].append((r[1], r[2], r[3]))
        return out

    def prune_alerts(self, before_ts):
        with self.lock, self.c:
            self.c.execute("DELETE FROM alerts WHERE ended IS NOT NULL AND ended<?", (before_ts,))

    # ---- measurements -------------------------------------------------------------

    def insert_samples(self, ts, rows):
        """rows: [(series_key, sent, lost, [rtt_ms, ...]), ...]"""
        with self.lock, self.c:
            self.c.executemany("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?)",
                               [(k, ts, s, l, pack(sorted(r))) for k, s, l, r in rows])

    def latest_all(self, since):
        rows = self._q(
            "SELECT s.series, s.ts, s.sent, s.lost, s.rtts FROM samples s "
            "JOIN (SELECT series, MAX(ts) m FROM samples WHERE ts>=? GROUP BY series) x "
            "ON s.series=x.series AND s.ts=x.m", (since,))
        return {r["series"]: summary(r["ts"], r["sent"], r["lost"], unpack(r["rtts"])) for r in rows}

    def rollup(self, now, raw_retention_s):
        """Summarize finished hours into 'hourly' and drop expired per-round data."""
        cur = now - now % 3600
        start = self.meta("rolled_until")
        if start is None:
            m = self._one("SELECT MIN(ts) FROM samples")[0]
            start = m - m % 3600 if m is not None else cur
        start = int(start)
        for h in range(start, cur, 3600):
            agg = {}
            for r in self._q("SELECT series, sent, lost, rtts FROM samples WHERE ts>=? AND ts<?", (h, h + 3600)):
                a = agg.setdefault(r["series"], [0, 0, []])
                a[0] += r["sent"]
                a[1] += r["lost"]
                a[2].extend(unpack(r["rtts"]))
            with self.lock, self.c:
                self.c.executemany("INSERT OR REPLACE INTO hourly VALUES (?,?,?,?,?)",
                                   [(k, h, a[0], a[1], pack(quantiles(a[2]))) for k, a in agg.items()])
                self.c.execute("INSERT OR REPLACE INTO meta VALUES ('rolled_until', ?)", (str(h + 3600),))
        with self.lock, self.c:
            self.c.execute("DELETE FROM samples WHERE ts<?", (now - raw_retention_s,))

    def import_measurements(self, table, rows):
        """Imported rounds (samples) or hourly summaries: [(series, ts, sent, lost, blob)]. Existing rows are kept."""
        assert table in ("samples", "hourly")
        with self.lock, self.c:
            before = self.c.total_changes
            self.c.executemany(f"INSERT OR IGNORE INTO {table} VALUES (?,?,?,?,?)", rows)
            return self.c.total_changes - before

    def import_mtr(self, rows):
        """Imported traceroutes: [(path, ts, reason, loss, hops, hubs_json, error, trigger_loss)], skipping known ones."""
        with self.lock, self.c:
            before = self.c.total_changes
            self.c.executemany("INSERT INTO mtr(path, ts, reason, loss, hops, hubs, error, trigger_loss) "
                               "SELECT ?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM mtr WHERE path=? AND ts=?)",
                               [(*r, r[0], r[1]) for r in rows])
            return self.c.total_changes - before

    def import_alerts(self, rows):
        """Imported finished alerts: [(series, target_id, family, kind, started, ended, peak_loss, last_loss, emailed,
        last_email)], skipping known ones."""
        with self.lock, self.c:
            before = self.c.total_changes
            self.c.executemany("INSERT INTO alerts(series, target_id, family, kind, started, ended, peak_loss, "
                               "last_loss, emailed, last_email) SELECT ?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS "
                               "(SELECT 1 FROM alerts WHERE series=? AND started=?)",
                               [(*r, r[0], r[4]) for r in rows])
            return self.c.total_changes - before

    def rollup_missing(self, keys):
        """Hourly summaries for imported rounds in hours that were already rolled up (the normal rollup only
        handles new hours). Returns the number of hours added."""
        rolled = self.meta("rolled_until")
        if rolled is None:
            return 0  # nothing rolled up yet: the next rollup starts at the oldest round
        n = 0
        for k in keys:
            hours = [r[0] for r in self._q("SELECT ts - ts % 3600 FROM samples WHERE series=? AND ts<? "
                                            "EXCEPT SELECT ts FROM hourly WHERE series=?", (k, int(rolled), k))]
            for h in hours:
                sent = lost = 0
                vals = []
                for r in self._q("SELECT sent, lost, rtts FROM samples WHERE series=? AND ts>=? AND ts<?",
                                 (k, h, h + 3600)):
                    sent += r["sent"]
                    lost += r["lost"]
                    vals.extend(unpack(r["rtts"]))
                with self.lock, self.c:
                    self.c.execute("INSERT OR IGNORE INTO hourly VALUES (?,?,?,?,?)",
                                   (k, h, sent, lost, pack(quantiles(vals))))
                n += 1
        return n

    def series(self, key, start, end, step, source):
        """Points per bucket of `step` seconds, each with merged quantiles."""
        if source == "raw":
            rows = self._q("SELECT ts, sent, lost, rtts FROM samples WHERE series=? AND ts>=? AND ts<=? ORDER BY ts",
                           (key, start, end))
        else:
            rolled = int(self.meta("rolled_until") or 0)
            rows = self._q("SELECT ts, sent, lost, q FROM hourly WHERE series=? AND ts>=? AND ts<=? ORDER BY ts",
                           (key, start, end))
            rows += self._q("SELECT ts, sent, lost, rtts FROM samples WHERE series=? AND ts>=? AND ts<=? ORDER BY ts",
                            (key, max(start, rolled), end))
        buckets = {}
        for ts, s, l, blob in rows:
            b = start + ((ts - start) // step) * step
            a = buckets.setdefault(b, [0, 0, []])
            a[0] += s
            a[1] += l
            a[2].extend(unpack(blob))
        return [{"t": b, "sent": a[0], "lost": a[1], "q": [round(x, 3) for x in quantiles(a[2])]}
                for b, a in sorted(buckets.items())]

    def last_ok(self, key):
        """Timestamp of the last round in which this series got at least one reply (None if never)."""
        r = self._one("SELECT MAX(ts) FROM samples WHERE series=? AND lost<sent", (key,))
        if r and r[0] is not None:
            return r[0]
        r = self._one("SELECT MAX(ts) FROM hourly WHERE series=? AND lost<sent", (key,))
        return r[0] + 3600 if r and r[0] is not None else None

    def oldest_raw(self):
        r = self._one("SELECT MIN(ts) FROM samples")
        return r[0] if r and r[0] is not None else None

    # ---- mtr ------------------------------------------------------------------------

    def insert_mtr(self, path, ts, reason, hubs, error=None, trigger_loss=None):
        """trigger_loss: packet loss (%) of the measurement round that started this traceroute."""
        loss = hubs[-1]["loss"] if hubs else 100.0
        with self.lock, self.c:
            self.c.execute("INSERT INTO mtr(path, ts, reason, loss, hops, hubs, error, trigger_loss) VALUES (?,?,?,?,?,?,?,?)",
                           (path, ts, reason, loss, len(hubs), json.dumps(hubs), error, trigger_loss))

    def last_mtr_times(self):
        return {r[0]: r[1] for r in self._q("SELECT path, MAX(ts) FROM mtr GROUP BY path")}

    def prune_mtr(self, before_ts):
        with self.lock, self.c:
            self.c.execute("DELETE FROM mtr WHERE ts<?", (before_ts,))

    @staticmethod
    def _mtr_row(r):
        return {"id": r["id"], "ts": r["ts"], "reason": r["reason"], "loss": r["loss"], "hops": r["hops"],
                "hubs": json.loads(r["hubs"]), "error": r["error"],
                "trigger_loss": r["trigger_loss"] if "trigger_loss" in r.keys() else None}

    def mtr_list(self, path, before=None, since=None, limit=30, issues_only=False):
        q, args = "SELECT * FROM mtr WHERE path=?", [path]
        if before:
            q += " AND ts<?"
            args.append(before)
        if since:
            q += " AND ts>=?"
            args.append(since)
        if issues_only:
            q += " AND reason!='scheduled'"
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [self._mtr_row(r) for r in self._q(q, args)]

    def mtr_at(self, path, t, window=1800):
        r = self._one("SELECT * FROM mtr WHERE path=? AND ts BETWEEN ? AND ? ORDER BY ABS(ts-?) LIMIT 1",
                      (path, t - window, t + window, t))
        return self._mtr_row(r) if r else None


def _every(v):
    """Measure every N rounds: None (inherit) or 1..60."""
    if v in ("", None):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError("Measure every must be a whole number of rounds")
    if not 1 <= v <= 60:
        raise ValueError("Measure every must be between 1 and 60 rounds")
    return v


def measure_every(target, groups):
    """Rounds between two measurements of a target: its own setting, else its group's (inherited), else 1.
    groups: {id: group} as returned by Store.groups()."""
    return target.get("measure_every") or (groups.get(target["group_id"]) or {}).get("measure_every_effective") or 1


def _rounds(v):
    """Rounds in a row before alerting: None (use the default) or 1..60."""
    if v in ("", None):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError("Rounds in a row must be a whole number")
    if not 1 <= v <= 60:
        raise ValueError("Rounds in a row must be between 1 and 60")
    return v


def _sort_mode(v):
    v = str(v or "manual").strip().lower()
    if v not in ("manual", "name"):
        raise ValueError("Target order must be 'manual' or 'name'")
    return v


def summary(ts, sent, lost, rtts):
    return {"ts": ts, "sent": sent, "lost": lost,
            "loss": round(lost / sent * 100, 1) if sent else 100.0,
            "median": round(median(sorted(rtts)), 3) if rtts else None}
