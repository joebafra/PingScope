"""Export and import of measurement history, next to the JSON export of groups, targets and settings.

File format: gzip-compressed JSON Lines (`.jsonl.gz`).
  line 1      the normal JSON export, with a `ref` (the target's id on the exporting server) on every target
              and a `history` summary
  lines 2..   {"t": "samples" | "hourly", "s": "<ref>-<4|6>", "r": [[ts, sent, lost, "<base64 float32s>"], ...]}
              {"t": "mtr", "r": [[path, ts, reason, loss, hops, hubs_json, error, trigger_loss], ...]}
              {"t": "alerts", "r": [[series, target_ref, family, kind, started, ended, peak_loss, last_loss,
                                     emailed, last_email], ...]}   (finished alerts only)
Targets get new ids on import; `ref` maps the history onto them. Rows that already exist are kept, so
importing the same file twice adds nothing.

Hourly summaries and the rollup bookmark (meta `rolled_until`): graphs of a month or a year read hourly rows
before the bookmark and per-round rows after it. So hourly rows at or after this server's bookmark are not
imported (the regular rollup makes them from the imported rounds), and a server without a bookmark (empty,
or after "Replace everything") gets one right after the last imported hour.
"""
import base64
import binascii
import gzip
import json
import sqlite3
import time
import zlib

CHUNK = 2000  # rows per line
MAX_BLOB = 4000  # bytes: at most 1000 float32 values per round or hour


def summary(con):
    """Counts and time span of the stored history (con: any sqlite3 connection to the database)."""
    n, first, last = con.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples").fetchone()
    h, hfirst = con.execute("SELECT COUNT(*), MIN(ts) FROM hourly").fetchone()
    m = con.execute("SELECT COUNT(*) FROM mtr").fetchone()[0]
    a = con.execute("SELECT COUNT(*) FROM alerts WHERE ended IS NOT NULL").fetchone()[0]
    starts = [x for x in (first, hfirst) if x is not None]
    return {"samples": n, "hourly": h, "mtr": m, "alerts": a,
            "from": min(starts) if starts else None, "to": last}


def export_stream(db_path, config):
    """Yield the gzip-compressed export. `config`: importer.export(store, refs=True).
    Reads from its own read-only connection in one snapshot, so the collector keeps writing meanwhile."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False, timeout=30)
    gz = zlib.compressobj(6, zlib.DEFLATED, 31)  # wbits 31: gzip container
    buf = []
    size = 0

    def line(obj):
        nonlocal size
        b = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        buf.append(b)
        size += len(b)

    def flush():
        nonlocal size
        out = gz.compress(b"".join(buf))
        buf.clear()
        size = 0
        return out

    try:
        con.execute("BEGIN")  # one consistent snapshot (WAL: doesn't block writers)
        refs = {t["ref"] for t in _targets(config)}
        line({**config, "history": {**summary(con), "exported": int(time.time())}})
        for table, col in (("samples", "rtts"), ("hourly", "q")):
            key, rows = None, []
            for series, ts, sent, lost, blob in con.execute(
                    f"SELECT series, ts, sent, lost, {col} FROM {table} ORDER BY series, ts"):
                if _ref(series) not in refs:
                    continue
                if series != key or len(rows) >= CHUNK:
                    if rows:
                        line({"t": table, "s": key, "r": rows})
                    key, rows = series, []
                rows.append([ts, sent, lost, base64.b64encode(blob).decode()])
                if size > 1 << 20:
                    yield flush()
            if rows:
                line({"t": table, "s": key, "r": rows})
        rows = []
        for r in con.execute("SELECT path, ts, reason, loss, hops, hubs, error, trigger_loss FROM mtr ORDER BY id"):
            rows.append(list(r))
            if len(rows) >= CHUNK // 4:  # traceroutes are larger rows
                line({"t": "mtr", "r": rows})
                rows = []
                if size > 1 << 20:
                    yield flush()
        if rows:
            line({"t": "mtr", "r": rows})
        rows = [list(r) for r in con.execute(
            "SELECT series, target_id, family, kind, started, ended, peak_loss, last_loss, emailed, last_email "
            "FROM alerts WHERE ended IS NOT NULL ORDER BY id") if r[1] in refs]
        for i in range(0, len(rows), CHUNK):
            line({"t": "alerts", "r": rows[i:i + CHUNK]})
        yield flush()
        yield gz.flush()
    finally:
        con.close()


def read_header(path):
    """The first line of an export file: the JSON export of groups, targets and settings."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.readline()


def restore(store, path, refs):
    """Insert the history lines of an export file. refs: {ref on the old server: target id here}."""
    done = {"samples": 0, "hourly": 0, "mtr": 0, "alerts": 0, "skipped": 0, "bad": 0}
    keys = set()
    rolled = store.meta("rolled_until")
    rolled = int(rolled) if rolled is not None else None
    last_hour = None

    def new_key(series):
        ref, _, fam = str(series).partition("-")
        tid = refs.get(_int(ref))
        return f"{tid}-{fam}" if tid is not None and fam in ("4", "6") else None

    with gzip.open(path, "rt", encoding="utf-8") as f:
        f.readline()  # the configuration, already imported
        for raw in f:
            try:
                rec = json.loads(raw)
                kind, rows = rec.get("t"), rec.get("r") or []
                if kind in ("samples", "hourly"):
                    key = new_key(rec.get("s"))
                    if key is None:
                        done["skipped"] += len(rows)
                        continue
                    clean = []
                    for ts, sent, lost, b64 in rows:
                        ts = int(ts)
                        if kind == "hourly":
                            if rolled is not None and ts >= rolled:
                                continue  # the regular rollup makes this hour from the imported rounds
                            last_hour = ts if last_hour is None else max(last_hour, ts)
                        blob = base64.b64decode(b64, validate=True)
                        if len(blob) % 4 or len(blob) > MAX_BLOB:
                            raise ValueError("bad measurement")
                        clean.append((key, ts, int(sent), int(lost), blob))
                    done[kind] += store.import_measurements(kind, clean)
                    keys.add(key)
                elif kind == "mtr":
                    clean = [(str(p), int(ts), str(reason), _num(loss), _int(hops), _hubs(hubs),
                              None if err is None else str(err), _num(trig))
                             for p, ts, reason, loss, hops, hubs, err, trig in rows]
                    done["mtr"] += store.import_mtr(clean)
                elif kind == "alerts":
                    clean = []
                    for series, tref, fam, akind, started, ended, peak, last, emailed, last_email in rows:
                        key, tid = new_key(series), refs.get(_int(tref))
                        if key is None or tid is None or ended is None:
                            done["skipped"] += 1
                            continue
                        clean.append((key, tid, int(fam), str(akind), int(started), int(ended), _num(peak),
                                      _num(last), 1 if emailed else 0, _int(last_email)))
                    done["alerts"] += store.import_alerts(clean)
            except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
                done["bad"] += 1
    if rolled is None and last_hour is not None:
        store.set_meta_if_missing("rolled_until", str(last_hour - last_hour % 3600 + 3600))
    done["hours"] = store.rollup_missing(keys)
    return done


def _targets(config):
    def walk(groups):
        for g in groups:
            yield from g.get("targets", [])
            yield from walk(g.get("groups", []))
    return walk(config.get("groups", []))


def _ref(series):
    return _int(str(series).partition("-")[0])


def _int(x):
    try:
        return int(x) if x is not None and str(x).lstrip("-").isdigit() else None
    except (TypeError, ValueError):
        return None


def _num(x):
    return None if x is None else float(x)


def _hubs(x):
    s = x if isinstance(x, str) else json.dumps(x)
    if len(s) > 200_000 or not isinstance(json.loads(s), list):
        raise ValueError("bad traceroute")
    return s
