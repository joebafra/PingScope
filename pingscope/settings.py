"""Runtime settings, stored in the database and editable from the admin panel."""

# key: (default, type, section, label, help, min, max)
SCHEMA = {
    "site_title": ("PingScope", str, "Site", "Site title", "Your own title, shown in the header and browser tab.", None, None),
    "site_remark": ("", str, "Site", "Welcome text", "Shown at the top of the overview page.", None, None),
    "theme": ("modern", str, "Site", "Dashboard style",
              "Modern: the default look, follows the visitor's light/dark preference. "
              "Classic: SmokePing-style with a dark menu, framed graphs and the familiar legend under every graph.",
              None, None),
    "public_dashboard": (True, bool, "Site", "Public dashboard",
                         "Allow viewing graphs without logging in. The admin panel always requires a login.", None, None),
    "public_show_hops": (False, bool, "Site", "Show traceroute hops to visitors",
                         "Show the router addresses along the path in mtr reports. Hostnames and IP addresses of "
                         "targets are never shown to visitors; logged-in admins always see everything.", None, None),

    "interval": (60, int, "Measurements", "Measurement interval (seconds)",
                 "Every target is measured once per interval.", 30, 3600),
    "ping_count": (20, int, "Measurements", "Pings per round", "ICMP echo requests per target per round.", 1, 100),
    "dns_count": (10, int, "Measurements", "DNS queries per round", "Queries per DNS target per round.", 1, 100),
    "tcp_count": (10, int, "Measurements", "TCP connects per round", "Connection attempts per TCP target per round.", 1, 100),
    "probe_spacing_ms": (1000, int, "Measurements", "Spacing between probes (ms)",
                         "Time between two probes to the same target within a round.", 100, 10000),
    "timeout_ms": (1500, int, "Measurements", "Probe timeout (ms)", "A probe without reply within this time counts as lost.", 100, 10000),
    "resolve_interval": (300, int, "Measurements", "Hostname re-resolve interval (seconds)",
                         "How often A/AAAA records of hostname targets are looked up again.", 60, 86400),

    "raw_retention_days": (14, int, "Storage", "Keep per-round data (days)",
                           "Full-resolution data. Older data is kept as hourly summaries forever.", 2, 365),

    "mtr_enabled": (True, bool, "Traceroutes (mtr)", "Enable mtr", "Run mtr traceroutes alongside the measurements.", None, None),
    "mtr_count": (10, int, "Traceroutes (mtr)", "Cycles per mtr report", "Number of pings per hop in each mtr report.", 1, 100),
    "mtr_interval": (900, int, "Traceroutes (mtr)", "Normal interval (seconds)", "How often mtr runs when everything is fine.", 60, 86400),
    "mtr_alert_interval": (60, int, "Traceroutes (mtr)", "Interval during problems (seconds)",
                           "How often mtr runs while a target has packet loss or is down.", 60, 3600),
    "mtr_alert_loss_pct": (0, int, "Traceroutes (mtr)", "Loss threshold (%)",
                           "Loss above this percentage counts as a problem. 0 = any lost packet.", 0, 99),
    "mtr_max_parallel": (16, int, "Traceroutes (mtr)", "Max parallel mtr processes", "Limits load during large outages.", 1, 128),
    "mtr_retention_days": (30, int, "Traceroutes (mtr)", "Keep mtr history (days)", "", 1, 365),
}

DEFAULTS = {k: v[0] for k, v in SCHEMA.items()}

# Settings that only accept a fixed set of values: key -> [(value, label), ...]
CHOICES = {
    "theme": [("modern", "Modern"), ("classic", "Classic (SmokePing-style)")],
}


def coerce(key, value):
    """Validate and convert a setting value. Raises ValueError with a readable message."""
    if key not in SCHEMA:
        raise ValueError(f"Unknown setting: {key}")
    default, typ, _, label, _, lo, hi = SCHEMA[key]
    if key in CHOICES:
        v = str(value or "").strip().lower()
        if v not in [c[0] for c in CHOICES[key]]:
            raise ValueError(f"{label}: choose one of {', '.join(c[0] for c in CHOICES[key])}")
        return v
    if typ is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if typ is int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: must be a whole number")
        if lo is not None and v < lo:
            raise ValueError(f"{label}: minimum is {lo}")
        if hi is not None and v > hi:
            raise ValueError(f"{label}: maximum is {hi}")
        return v
    return str(value if value is not None else "").strip()[:2000]


def check(s):
    """Return warnings for setting combinations that don't fit together."""
    w = []
    worst = max(s["ping_count"], s["dns_count"], s["tcp_count"]) * s["probe_spacing_ms"] + s["timeout_ms"]
    if worst > s["interval"] * 900:
        w.append(
            f"Probes per round × spacing ({worst / 1000:.0f}s) is too long for the {s['interval']}s interval; "
            "rounds may overlap. Lower the counts or spacing, or raise the interval."
        )
    return w


def schema_for_ui():
    return [
        {"key": k, "default": v[0], "type": v[1].__name__, "section": v[2], "label": v[3], "help": v[4],
         "min": v[5], "max": v[6], "options": CHOICES.get(k)}
        for k, v in SCHEMA.items()
    ]
