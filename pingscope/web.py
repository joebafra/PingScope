"""HTTP layer: dashboard + public API, login, and the admin API."""
import asyncio
import contextlib
import html
import logging
import math
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, alerts as alerts_mod, importer, settings as settings_mod
from .collector import Collector, measure
from .db import Store
from .model import FAMILY_MODES, DNS_TYPES, ValidationError, clean_target, ip_version
from .probes import PROBES
from .resolver import Resolver, Series, lookup

log = logging.getLogger("pingscope")

DB_PATH = os.environ.get("PINGSCOPE_DB", "data/pingscope.db")
STATIC = Path(__file__).parent / "static"
COOKIE = "pingscope_session"
SESSION_TTL = 14 * 86400
RANGES = {"3h": 3 * 3600, "1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "1y": 365 * 86400}
SERIES_RE = re.compile(r"^\d+-[46]$")

store = Store(DB_PATH)
collector = Collector(store)
STATE = {"setup_code": None}
_login_failures = {}


@contextlib.asynccontextmanager
async def lifespan(app):
    if store.user_count() == 0:
        STATE["setup_code"] = secrets.token_hex(4).upper()
        log.warning("=" * 64)
        log.warning("No admin account yet. Open /admin and use setup code: %s", STATE["setup_code"])
        log.warning("(or run: python -m pingscope create-admin)")
        log.warning("=" * 64)
    task = asyncio.create_task(collector.run())
    yield
    task.cancel()


app = FastAPI(title="PingScope", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/static/"):
        # files are referenced with ?v=<hash>; a new version gets a new URL, so they can be cached long
        resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable"
                                if request.url.query.startswith("v=") else "no-cache")
    return resp


# ---- auth helpers ---------------------------------------------------------------

def current_user(request: Request):
    return store.session_user(request.cookies.get(COOKIE))


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Login required")
    return u


def _csrf(request):
    if request.method not in ("GET", "HEAD") and request.headers.get("x-pingscope") != "1":
        raise HTTPException(403, "Missing request header")  # CSRF protection


def require_admin(request: Request):
    u = require_user(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Your account has view-only access")
    _csrf(request)
    return u


def can_view(request: Request):
    if store.settings()["public_dashboard"]:
        return None
    return require_user(request)


def _is_https(request):
    return request.url.scheme == "https"


def _login(request, response, user):
    token = secrets.token_urlsafe(32)
    store.create_session(user["id"], token, SESSION_TTL)
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=_is_https(request), path="/")


def _client_ip(request):
    # uvicorn already resolves X-Forwarded-For for trusted proxies (PINGSCOPE_TRUSTED_PROXIES)
    return request.client.host if request.client else "?"


def _throttle(request):
    ip, now = _client_ip(request), time.time()
    hits = [t for t in _login_failures.get(ip, []) if now - t < 600]
    _login_failures[ip] = hits
    if len(hits) >= 10:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")


# ---- pages --------------------------------------------------------------------------

def _asset_version():
    """Short hash of all static files: changes with every update, so browsers never mix old and new files."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted(STATIC.iterdir()):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def _page(name):
    html = (STATIC / name).read_text(encoding="utf-8")
    html = re.sub(r'(static/[\w.-]+\.(?:js|css))"', lambda m: f'{m.group(1)}?v={ASSET_VERSION}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    resp = _page("index.html")
    theme = store.settings()["theme"]
    resp.body = resp.body.replace(b'<html lang="en">', f'<html lang="en" data-theme="{theme}">'.encode(), 1)
    resp.headers["content-length"] = str(len(resp.body))
    return resp


@app.get("/admin")
def admin_page():
    return _page("admin.html")


# ---- session ------------------------------------------------------------------------

@app.get("/api/session")
def session(request: Request):
    s = store.settings()
    return {"user": current_user(request), "setup_required": store.user_count() == 0,
            "public": s["public_dashboard"], "site_title": s["site_title"], "version": __version__}


@app.post("/api/login")
def login(request: Request, response: Response, body: dict = Body(...)):
    _throttle(request)
    user = store.check_login(body.get("username"), body.get("password"))
    if not user:
        _login_failures.setdefault(_client_ip(request), []).append(time.time())
        raise HTTPException(401, "Wrong username or password")
    _login(request, response, user)
    return {"user": user}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    tok = request.cookies.get(COOKIE)
    if tok:
        store.delete_session(tok)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.post("/api/setup")
def setup(request: Request, response: Response, body: dict = Body(...)):
    _throttle(request)
    if store.user_count() > 0:
        raise HTTPException(400, "Setup is already done")
    if not STATE["setup_code"] or not secrets.compare_digest(
            str(body.get("code", "")).strip().upper(), STATE["setup_code"]):
        _login_failures.setdefault(_client_ip(request), []).append(time.time())
        raise HTTPException(401, "Wrong setup code. Check the PingScope log output.")
    try:
        uid = store.create_user(body.get("username"), body.get("password"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    STATE["setup_code"] = None
    user = {"id": uid, "username": body.get("username").strip(), "role": "admin"}
    _login(request, response, user)
    return {"user": user}


# ---- public data -----------------------------------------------------------------------

def _series_json(x, private=True):
    if not private:  # visitors: no hostnames, addresses or error texts that contain them
        return {"key": x.key, "family": x.family, "unresolved": x.address is None,
                "mtr": bool(x.path), "last": collector.latest.get(x.key)}
    return {"key": x.key, "family": x.family, "address": x.address, "error": x.error, "via": x.via,
            "unresolved": x.address is None, "mtr": bool(x.path), "path": x.path,
            "last": collector.latest.get(x.key)}


def _visible_to(request, tid):
    """Visitors may only read enabled targets outside hidden groups; logged-in admins may read everything."""
    if current_user(request):
        return True
    t = store.target(tid)
    return bool(t and t["enabled"] and t["group_id"] not in store.hidden_group_ids())


def _find_series(key):
    tid = int(key.split("-")[0])
    return next((x for x in collector.series.get(tid, []) if x.key == key), None)


@app.get("/api/tree")
def tree(request: Request, _=Depends(can_view)):
    s = store.settings()
    user = current_user(request)
    admin = user is not None  # admins and viewers see the same dashboard; only admins get edit links
    groups = store.groups()
    if not admin:  # hidden groups (and everything below them) don't exist for visitors
        groups = [g for g in groups if not g["hidden_effective"]]
        for g in groups:
            g.pop("hidden", None)
            g.pop("hidden_effective", None)
            for k in ("alert_mode", "alert_loss", "alert_down", "alert_email", "alert_effective"):
                g.pop(k, None)
    visible = {g["id"] for g in groups}
    targets = []
    for t in store.targets(enabled_only=True):
        if t["group_id"] not in visible:
            continue
        item = {
            "id": t["id"], "group_id": t["group_id"], "name": t["name"], "probe": t["probe"], "families": t["families"],
            "series": [_series_json(x, admin) for x in collector.series.get(t["id"], [])],
        }
        if admin:  # the DNS query name is never shown on the dashboard, only in the admin edit form
            item.update(host=t["host"], host_v6=t["host_v6"],
                        port=t["port"] if t["probe"] != "ping" else None)
        targets.append(item)
    return {
        "site": {"title": s["site_title"], "remark": s["site_remark"], "theme": s["theme"]},
        "groups": groups, "targets": targets,
        "probes": {k: p.label for k, p in PROBES.items()},
        "interval": s["interval"], "last_run": collector.last_run, "now": int(time.time()),
        "mtr": bool(s["mtr_enabled"] and collector.mtr.available), "mtr_interval": s["mtr_interval"],
        "logged_in": admin, "can_edit": bool(user and user["role"] == "admin"),
    }


@app.get("/api/series")
def series(request: Request, key: str, range: str = "1d", points: int = Query(600, ge=50, le=2000),
           _=Depends(can_view)):
    if not SERIES_RE.match(key):
        raise HTTPException(400, "Invalid series")
    if not _visible_to(request, int(key.split("-")[0])):
        raise HTTPException(404, "Not found")
    if range not in RANGES:
        raise HTTPException(400, "Unknown range")
    s = store.settings()
    sec = RANGES[range]
    source = "raw" if sec <= min(7 * 86400, s["raw_retention_days"] * 86400) else "hourly"
    native = s["interval"] if source == "raw" else 3600
    step = max(native, math.ceil(sec / points / native) * native)
    end = int(time.time())
    start = end - sec
    start -= start % step
    return {"key": key, "start": start, "end": end, "step": step, "source": source,
            "points": store.series(key, start, end, step, source)}


def _mask_hops(runs, show_hops):
    """Visitors never see the target's address; intermediate routers only when enabled in settings."""
    for r in runs:
        hubs = r["hubs"]
        for i, h in enumerate(hubs):
            if h["host"] == "???":
                continue
            if i == len(hubs) - 1:
                h["host"] = "destination"
            elif not show_hops:
                h["host"] = "hidden"
        if r.get("error"):
            r["error"] = "mtr could not run"
    return runs


@app.get("/api/mtr")
def mtr(request: Request, key: str, before: int | None = None, since: int | None = None, at: int | None = None,
        limit: int = Query(30, ge=1, le=500), only_issues: bool = False, _=Depends(can_view)):
    if not SERIES_RE.match(key):
        raise HTTPException(400, "Invalid series")
    if not _visible_to(request, int(key.split("-")[0])):
        raise HTTPException(404, "Not found")
    x = _find_series(key)
    if not x or not x.path:
        return {"runs": [], "more": False}
    if at is not None:
        r = store.mtr_at(x.path, at)
        runs, more = ([r] if r else []), False
    else:
        runs = store.mtr_list(x.path, before=before, since=since, limit=limit + 1, issues_only=only_issues)
        runs, more = runs[:limit], len(runs) > limit
    if not current_user(request):
        _mask_hops(runs, store.settings()["public_show_hops"])
    return {"runs": runs, "more": more}


# ---- admin API ----------------------------------------------------------------------------

admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _bad(e):
    if isinstance(e, ValidationError):
        return HTTPException(400, {"message": "Please fix the highlighted fields", "errors": e.errors})
    return HTTPException(400, str(e))


def _public_settings(s):
    """Settings for the admin UI: secrets are never sent back, only whether they are set."""
    s = dict(s)
    for k in settings_mod.SECRETS:
        s[k + "_set"] = bool(s[k]) or bool(os.environ.get(settings_mod.SECRET_ENV.get(k, ""), ""))
        s[k] = ""
    return s


@admin.get("/data")
def admin_data():
    s = store.settings()
    return {
        "groups": store.groups(), "targets": store.targets(), "contacts": store.contacts(),
        "series": {tid: [_series_json(x) for x in xs] for tid, xs in collector.series.items()},
        "probes": [p.info() for p in PROBES.values()],
        "families": FAMILY_MODES, "dns_types": DNS_TYPES,
        "settings": _public_settings(s), "settings_schema": settings_mod.schema_for_ui(),
        "warnings": settings_mod.check(s) + [w for w in collector.warnings if w not in settings_mod.check(s)]
                    + ([collector.alerter.last_error] if collector.alerter.last_error else []),
        "status": {"last_run": collector.last_run, "duration": collector.last_duration,
                   "mtr_available": collector.mtr.available,
                   "fping_available": PROBES["ping"].available()},
    }


@admin.post("/groups")
def create_group(body: dict = Body(...)):
    try:
        gid = store.create_group(body.get("name"), body.get("parent_id"), body.get("sort_mode") or "manual",
                                 bool(body.get("hidden")))
        if body.get("alert_mode"):
            store.set_alerts(gid, body["alert_mode"], body.get("alert_loss"), bool(body.get("alert_down", True)),
                             bool(body.get("alert_email", True)), bool(body.get("alert_telegram", True)))
    except ValueError as e:
        raise _bad(e)
    return {"id": gid}


@admin.put("/groups/{gid}")
def update_group(gid: int, body: dict = Body(...)):
    try:
        store.update_group(gid, body.get("name"), body.get("parent_id", "keep"))
        if body.get("sort_mode"):
            store.set_sort_mode(gid, body["sort_mode"], bool(body.get("sort_subgroups")))
        if "hidden" in body:
            store.set_hidden(gid, bool(body["hidden"]))
        if "alert_mode" in body:
            store.set_alerts(gid, body["alert_mode"], body.get("alert_loss"), bool(body.get("alert_down", True)),
                             bool(body.get("alert_email", True)), bool(body.get("alert_telegram", True)))
    except KeyError:
        raise HTTPException(404, "Group not found")
    except ValueError as e:
        raise _bad(e)
    collector.poke()
    return {"ok": True}


@admin.delete("/groups/{gid}")
def delete_group(gid: int):
    if not store.group(gid):
        raise HTTPException(404, "Group not found")
    n = store.delete_group(gid)
    collector.poke()
    return {"deleted_targets": n}


@admin.post("/reorder")
def reorder(body: dict = Body(...)):
    kind = body.get("kind")
    ids = body.get("ids") or []
    if kind not in ("group", "target") or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "Invalid reorder request")
    store.reorder(kind, ids)
    return {"ok": True}


@admin.post("/targets")
def create_target(body: dict = Body(...)):
    try:
        t = clean_target(body, list(PROBES))
        tid = store.create_target(int(body.get("group_id") or 0), t)
    except (ValueError, TypeError) as e:
        raise _bad(e)
    collector.poke()
    return {"id": tid}


@admin.put("/targets/{tid}")
def update_target(tid: int, body: dict = Body(...)):
    try:
        t = clean_target(body, list(PROBES))
        store.update_target(tid, t, int(body["group_id"]) if body.get("group_id") else None)
    except KeyError:
        raise HTTPException(404, "Target not found")
    except (ValueError, TypeError) as e:
        raise _bad(e)
    collector.poke()
    return {"ok": True}


@admin.post("/targets/bulk")
def bulk_targets(body: dict = Body(...)):
    ids = [int(i) for i in body.get("ids") or []]
    action = body.get("action")
    if not ids:
        raise HTTPException(400, "No targets selected")
    if action == "delete":
        store.delete_targets(ids)
    elif action in ("enable", "disable"):
        store.set_targets(ids, enabled=int(action == "enable"))
    elif action in ("alerts_off", "alerts_on"):
        store.set_targets(ids, alert_skip=int(action == "alerts_off"))
    elif action == "move":
        gid = int(body.get("group_id") or 0)
        if not store.group(gid):
            raise HTTPException(400, "Group not found")
        store.set_targets(ids, group_id=gid)
    elif action == "set_ipv6":
        for item in body.get("items") or []:
            t = store.target(int(item["id"]))
            if t:
                try:
                    store.update_target(t["id"], clean_target({**t, "host_v6": item.get("host_v6", "")}, list(PROBES)))
                except ValueError as e:
                    raise _bad(e)
    else:
        raise HTTPException(400, "Unknown action")
    collector.poke()
    return {"ok": True}


@admin.post("/test")
async def test_target(body: dict = Body(...)):
    """Measure a (possibly unsaved) target right now with a few probes."""
    try:
        t = clean_target(body, list(PROBES))
    except ValueError as e:
        raise _bad(e)
    t["id"] = 0
    s = store.settings()
    s = {**s, "probe_spacing_ms": 300}
    t["count"] = 3
    series = await Resolver().plan([t], 0)
    results, errors = await measure(series, s)
    out = []
    for x in series:
        r = results.get(x.key)
        if r is None:
            out.append({"family": x.family, "address": x.address, "error": f"Could not measure: {errors.get(x.target['probe'], 'probe failed')}",
                        "sent": 0, "lost": 0, "min": None, "max": None, "median": None})
            continue
        rt = sorted(r.rtts)
        out.append({"family": x.family, "address": x.address, "error": x.error, "sent": r.sent, "lost": r.lost,
                    "min": rt[0] if rt else None, "max": rt[-1] if rt else None,
                    "median": rt[len(rt) // 2] if rt else None})
    return {"results": out}


@admin.post("/resolve")
async def resolve(body: dict = Body(...)):
    name = str(body.get("name") or "").strip()
    if not name or ip_version(name):
        return {"a": [], "aaaa": []}
    a, aaaa = await asyncio.gather(lookup(name, 4), lookup(name, 6))
    return {"a": a, "aaaa": aaaa}


@admin.get("/offline")
def offline():
    """Enabled targets where every series is down (no replies) or unresolved, with how long they've been down."""
    out = []
    for t in store.targets(enabled_only=True):
        xs = collector.series.get(t["id"], [])
        if not xs:
            continue
        lasts = [collector.latest.get(x.key) for x in xs]
        if not all(x.address is None or (lt and lt["loss"] >= 100) for x, lt in zip(xs, lasts)):
            continue
        oks = [v for v in (store.last_ok(x.key) for x in xs) if v]
        last_ok = max(oks) if oks else None
        out.append({
            "id": t["id"], "last_ok": last_ok, "since": last_ok or t["created"] or None, "never": last_ok is None,
            "reason": "unresolved" if any(x.address is None for x in xs) else "no reply",
            "families": [x.family for x in xs],
        })
    out.sort(key=lambda o: o["since"] or 0)
    return {"targets": out, "now": int(time.time())}


@admin.get("/ipv6")
async def ipv6_suggestions():
    """Targets without IPv6 monitoring, with AAAA candidates found via their host or name."""
    have6 = {tid for tid, xs in collector.series.items() if any(x.family == 6 for x in xs)}
    todo = [t for t in store.targets() if t["families"] != "ipv4" and not t["host_v6"] and t["id"] not in have6]
    names = sorted({n for t in todo for n in importer.ipv6_candidates(t)})
    sem = asyncio.Semaphore(20)

    async def one(n):
        async with sem:
            return n, await lookup(n, 6)

    found = dict(await asyncio.gather(*[one(n) for n in names]))
    groups = {g["id"]: g for g in store.groups()}

    def path(gid):
        out = []
        while gid and gid in groups:
            out.insert(0, groups[gid]["name"])
            gid = groups[gid]["parent_id"]
        return " › ".join(out)

    items = []
    for t in todo:
        cands = [{"name": n, "addresses": found.get(n, [])} for n in importer.ipv6_candidates(t)]
        items.append({"id": t["id"], "name": t["name"], "probe": t["probe"], "host": t["host"],
                      "group": path(t["group_id"]), "candidates": [c for c in cands if c["addresses"]]})
    total = len(store.targets())
    return {"items": items, "total": total, "covered": total - len(todo)}


@admin.post("/import/preview")
def import_preview(body: dict = Body(...)):
    try:
        tree, warnings, kind = importer.parse_any(str(body.get("text") or ""))
    except ValueError as e:
        raise _bad(e)
    stats = importer.prepare(tree, list(PROBES), bool(body.get("add_known_ipv6", True)), warnings)
    return {"kind": kind, "site": tree.get("site"), "stats": stats, "outline": importer.outline(tree),
            "warnings": warnings[:200], "warning_count": len(warnings)}


@admin.post("/import")
def import_apply(body: dict = Body(...)):
    try:
        tree, warnings, kind = importer.parse_any(str(body.get("text") or ""))
    except ValueError as e:
        raise _bad(e)
    importer.prepare(tree, list(PROBES), bool(body.get("add_known_ipv6", True)), warnings)
    mode = "replace" if body.get("mode") == "replace" else "merge"
    added = importer.apply(store, tree, mode)
    site = tree.get("site") or {}
    if body.get("use_site_title") and site.get("title"):
        store.save_settings({"site_title": site["title"], "site_remark": site.get("remark", "")})
    if kind == "json" and body.get("restore_settings") and tree.get("settings"):
        try:
            store.save_settings({k: v for k, v in tree["settings"].items()
                                 if k in settings_mod.SCHEMA and k not in settings_mod.SECRETS})
        except ValueError as e:
            raise _bad(e)
    collector.poke()
    return {"added": added}


@admin.get("/export")
def export():
    data = importer.export(store)
    name = f"pingscope-export-{time.strftime('%Y%m%d-%H%M')}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _check_https(domain):
    """Check the certificate Caddy serves for `domain` and that HTTP redirects to HTTPS."""
    import datetime
    import http.client
    import socket
    import ssl
    out = {"domain": domain, "certificate": None, "redirect": None, "error": None}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(("127.0.0.1", 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        expires = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
        out["certificate"] = {
            "valid": True, "issuer": issuer.get("organizationName") or issuer.get("commonName", ""),
            "expires": int(expires.timestamp()),
            "days_left": (expires - datetime.datetime.now(datetime.timezone.utc)).days,
            "names": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"],
        }
    except ssl.SSLCertVerificationError as e:
        out["certificate"] = {"valid": False}
        out["error"] = f"Certificate not valid: {e.verify_message}"
    except ssl.SSLError as e:
        out["error"] = f"No valid certificate for {domain} yet ({e.reason or e}). Check that DNS points to this server and port 80 is open."
    except OSError as e:
        out["error"] = f"No HTTPS on port 443 ({e.strerror or e}). Is Caddy running?"
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 80, timeout=5)
        conn.request("GET", "/", headers={"Host": domain})
        r = conn.getresponse()
        loc = r.getheader("Location") or ""
        out["redirect"] = r.status in (301, 302, 307, 308) and loc.startswith("https://")
        conn.close()
    except OSError:
        out["redirect"] = False
    return out


@admin.get("/https")
async def https_status(request: Request):
    domains = [d.strip() for d in os.environ.get("PINGSCOPE_DOMAIN", "").split(",") if d.strip()]
    checks = await asyncio.gather(*[asyncio.to_thread(_check_https, d) for d in domains])
    return {"configured": bool(domains), "this_request_https": _is_https(request), "domains": checks}


@admin.put("/settings")
def save_settings(body: dict = Body(...)):
    body = {k: v for k, v in body.items() if not k.endswith("_set")}
    for k in settings_mod.SECRETS:  # empty = keep the stored secret
        if not str(body.get(k) or "").strip():
            body.pop(k, None)
    try:
        s = store.save_settings(body)
    except ValueError as e:
        raise _bad(e)
    collector.poke()
    return {"settings": _public_settings(s), "warnings": settings_mod.check(s)}


@admin.get("/alerts")
def alert_list():
    targets = {t["id"]: t for t in store.targets()}
    groups = {g["id"]: g for g in store.groups()}

    def path(gid):
        out = []
        while gid in groups:
            out.insert(0, groups[gid]["name"])
            gid = groups[gid]["parent_id"]
        return " › ".join(out)

    rows = []
    for a in store.alert_list(300):
        t = targets.get(a["target_id"])
        if t:
            rows.append({**a, "name": t["name"], "group": path(t["group_id"])})
    s = store.settings()
    return {"alerts": rows, "now": int(time.time()), "email_problem": alerts_mod.email_ready(s),
            "telegram_problem": alerts_mod.telegram_ready(alerts_mod.with_contacts(s, store)),
            "target_chats": sum(1 for t in targets.values() if t.get("alert_chat_ids")),
            "last_error": collector.alerter.last_error, "last_sent": collector.alerter.last_sent,
            "groups_on": sum(1 for g in groups.values() if g["alert_mode"] == "on")}


@admin.post("/alerts/preview")
def alert_preview(body: dict = Body(...)):
    """How many targets would have alerted in the past days with these group settings."""
    gid = body.get("group_id")
    parent = body.get("parent_id")
    groups = {g["id"]: g for g in store.groups()}
    try:
        loss = int(body["alert_loss"]) if str(body.get("alert_loss") or "").strip() else None
    except ValueError:
        raise HTTPException(400, "Packet loss threshold must be a whole number")
    down = bool(body.get("alert_down", True))
    s = store.settings()
    need = int(body.get("after_rounds") or s["alert_after_rounds"])
    days = max(1, min(int(body.get("days") or 7), s["raw_retention_days"]))
    # groups that follow this group's setting: itself + subgroups whose path down to it is all "inherit"
    members = set()
    if gid in groups:
        members.add(gid)
        changed = True
        while changed:
            changed = False
            for g in groups.values():
                if g["id"] not in members and g["parent_id"] in members and g["alert_mode"] == "inherit":
                    members.add(g["id"])
                    changed = True
    names = {}

    def path(g_id):
        if g_id not in names:
            out, x = [], g_id
            while x in groups:
                out.insert(0, groups[x]["name"])
                x = groups[x]["parent_id"]
            names[g_id] = " › ".join(out)
        return names[g_id]

    targets = [t for t in store.targets(enabled_only=True) if t["group_id"] in members]
    skipped = [t for t in targets if t["alert_skip"]]
    active = [t for t in targets if not t["alert_skip"]]
    since = int(time.time()) - days * 86400
    keys = {f"{t['id']}-{f}": (t, f) for t in active for f in (4, 6)}
    hist = store.loss_rounds(keys, since)
    rows, emails, n_alerts = [], set(), 0
    for key, rounds in hist.items():
        if not rounds:
            continue
        t, fam = keys[key]
        runs = alerts_mod.simulate(rounds, loss, down, need)
        if not runs:
            continue
        n_alerts += len(runs)
        for st, en, _, _ in runs:
            emails.add(st)
            if en and s["alert_recovery"]:
                emails.add(en)
        rows.append({"target_id": t["id"], "name": t["name"], "group": path(t["group_id"]), "family": fam,
                     "alerts": len(runs), "down": sum(1 for r in runs if r[2] == "down"),
                     "minutes": sum(((r[1] or int(time.time())) - r[0]) // 60 for r in runs),
                     "worst": max(r[3] for r in runs), "open": runs[-1][1] is None})
    rows.sort(key=lambda r: (-r["alerts"], -r["minutes"]))
    return {"days": days, "targets": len(active), "skipped": [{"id": t["id"], "name": t["name"]} for t in skipped],
            "targets_alerting": len({r["target_id"] for r in rows}), "alerts": n_alerts, "emails": len(emails),
            "rows": rows[:100], "need": need, "has_data": any(hist.values())}


@admin.get("/telegram/chats")
async def telegram_chats():
    try:
        return {"chats": await asyncio.to_thread(alerts_mod.telegram_chats, store.settings())}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@admin.get("/contacts")
def contacts():
    return {"contacts": store.contacts()}


@admin.post("/contacts")
def add_contact(body: dict = Body(...)):
    try:
        return {"id": store.save_contact(None, body.get("chat_id"), body.get("label"), body.get("is_default"))}
    except ValueError as e:
        raise _bad(e)


@admin.put("/contacts/{cid}")
def update_contact(cid: int, body: dict = Body(...)):
    try:
        store.save_contact(cid, body.get("chat_id"), body.get("label"), body.get("is_default"))
    except KeyError:
        raise HTTPException(404, "Contact not found")
    except ValueError as e:
        raise _bad(e)
    collector.poke()
    return {"ok": True}


@admin.delete("/contacts/{cid}")
def delete_contact(cid: int):
    return {"removed_from_targets": store.delete_contact(cid)}


@admin.post("/alerts/test-alert")
async def alert_test_alert(body: dict = Body(default={})):
    """A sample alert like the real ones. Without chat_ids: to the admin channels; with: to those chats only."""
    targets = store.targets(enabled_only=True)
    tid = body.get("target_id")
    t = next((x for x in targets if x["id"] == tid), None) if tid else (targets[0] if targets else None)
    if not t:
        raise HTTPException(400, "Add a target first")
    xs = collector.series.get(t["id"]) or []
    x = xs[0] if xs else Series(f"{t['id']}-4", t, 4, t["host"] or None)
    chats = alerts_mod.chat_list(body.get("chat_ids")) if "chat_ids" in body else None
    if chats is not None and not chats:
        raise HTTPException(400, "Select or enter a chat ID first")
    r = await collector.alerter.test_alert(store.settings(), x, chats)
    if r["errors"] and not r["sent"]:
        raise HTTPException(400, "; ".join(r["errors"]))
    return r


@admin.post("/alerts/test-telegram")
async def alert_test_telegram(body: dict = Body(default={})):
    s = store.settings()
    chats = alerts_mod.chat_list(body.get("chat_id")) or alerts_mod.chat_list(alerts_mod.with_contacts(s, store)["telegram_chat_ids"])
    if not chats:
        raise HTTPException(400, "No Telegram chat ID set. Add one in Settings → Notifications.")
    site = html.escape(s.get("site_title") or "PingScope")
    msg = (f"✅ <b>{site}</b>\nTest message from PingScope. Alerts "
           + ("for this connection " if body.get("chat_id") else "") + "will arrive in this chat.")
    try:
        for c in chats:
            await asyncio.to_thread(alerts_mod.send_telegram, s, c, msg)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "to": chats}


@admin.post("/alerts/test")
async def alert_test():
    s = store.settings()
    site = s.get("site_title") or "PingScope"
    try:
        await asyncio.to_thread(
            alerts_mod.send_email, s, f"[{site}] Test email",
            f"This is a test email from {site}. Alert emails will look like this and come from this address.",
            f'<div style="font-family:sans-serif;font-size:14px"><h2 style="font-size:18px">{site}</h2>'
            f'<p>This is a test email from PingScope. Alert emails will come from this address.</p></div>')
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "to": alerts_mod.recipients(s)}


@admin.get("/users")
def users():
    return {"users": store.users()}


@admin.post("/users")
def add_user(body: dict = Body(...)):
    try:
        return {"id": store.create_user(body.get("username"), body.get("password"), body.get("role") or "admin")}
    except ValueError as e:
        raise _bad(e)


@admin.put("/users/{uid}")
def update_user(uid: int, body: dict = Body(...), user=Depends(require_admin)):
    role = body.get("role")
    if uid == user["id"] and role != "admin":
        raise HTTPException(400, "You can't take admin rights away from your own account")
    try:
        store.set_role(uid, role)
    except ValueError as e:
        raise _bad(e)
    return {"ok": True}


@admin.delete("/users/{uid}")
def delete_user(uid: int, user=Depends(require_admin)):
    if uid == user["id"]:
        raise HTTPException(400, "You can't delete your own account")
    store.delete_user(uid)
    return {"ok": True}


def require_user_csrf(request: Request):
    u = require_user(request)
    _csrf(request)
    return u


@app.post("/api/password")  # every user (admin or viewer) can change their own password
def change_password(request: Request, response: Response, body: dict = Body(...), user=Depends(require_user_csrf)):
    if not store.check_login(user["username"], body.get("current")):
        raise HTTPException(400, "Current password is wrong")
    try:
        store.set_password(user["id"], body.get("new"))
    except ValueError as e:
        raise _bad(e)
    _login(request, response, user)  # other sessions were logged out
    return {"ok": True}


app.include_router(admin)
