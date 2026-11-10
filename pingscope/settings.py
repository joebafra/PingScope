"""Runtime settings, stored in the database and editable from the admin panel."""

# key: (default, type, section, label, help, min, max)
SCHEMA = {
    "site_title": ("PingScope", str, "Site", "Site title", "Your own title, shown in the header and browser tab.", None, None),
    "site_remark": ("", str, "Site", "Welcome text", "Shown at the top of the overview page.", None, None),
    "theme": ("modern", str, "Site", "Dashboard style",
              "Modern: the default look, follows the visitor's light/dark preference. "
              "Classic: SmokePing-style with a dark menu, framed graphs and the familiar legend under every graph.",
              None, None),
    "show_icons": (True, bool, "Site", "Show icons",
                   "Small line icons next to labels: a lock for hidden groups, a bell for notifications, a globe for "
                   "groups that show IP addresses, a magnifier when zoomed in. Off shows text only. Buttons that "
                   "consist of just an icon (copy, light/dark) keep it.", None, None),
    "periods": ("3h,1d,7d,30d,1y", "multi", "Site", "Periods",
                "The periods offered in the header and shown on a target page, from short to long.", None, None),
    "time_zone": ("browser", str, "Site", "Time zone of the times shown",
                  "Visitor: every visitor sees times in their own browser's time zone. Server: everyone sees the "
                  "server's time zone (the TZ setting of the server, shown under Status), like SmokePing. Choose Server "
                  "when visitors' browsers report UTC, for example because of privacy settings.", None, None),
    "public_dashboard": (True, bool, "Site", "Public dashboard",
                         "Allow viewing graphs without logging in. The read-only API the dashboard uses (/api/tree, "
                         "/api/series, /api/mtr, /api/history) follows this setting: turn it off to require a login for "
                         "both. The admin panel always requires a login.", None, None),
    "open_api": (False, bool, "Site", "Open API for scripts",
                 "Off: the read-only API only answers the dashboard in the browser and logged-in users; scripts, "
                 "robots and scanners get 404 Not Found. On: anyone who may view the dashboard can also fetch its "
                 "data as JSON, for example for your own scripts. Either way the API never shows more than the "
                 "dashboard.", None, None),
    "public_show_hops": (False, bool, "Site", "Show traceroute hops to visitors",
                         "Show the router addresses along the path in mtr reports. Hostnames and IP addresses of "
                         "targets are only shown to visitors in groups set to show them (Edit group); logged-in "
                         "users always see everything.", None, None),

    "interval": (60, int, "Measurements", "Measurement interval (seconds)",
                 "Every target is measured once per interval.", 30, 3600),
    "ping_count": (20, int, "Measurements", "Pings per round", "ICMP echo requests per target per round.", 1, 100),
    "dns_count": (10, int, "Measurements", "DNS queries per round", "Queries per DNS target per round.", 1, 100),
    "tcp_count": (10, int, "Measurements", "TCP connects per round", "Connection attempts per TCP target per round.", 1, 100),
    "probe_spacing_ms": (1000, int, "Measurements", "Spacing between probes (ms)",
                         "Time between two probes to the same target within a round.", 100, 10000),
    "timeout_ms": (1500, int, "Measurements", "Probe timeout (ms)", "A probe without reply within this time counts as lost.", 100, 10000),
    "ping_size": (56, int, "Measurements", "Ping packet size (bytes)",
                  "ICMP data bytes per ping, like SmokePing's packetsize. 56 gives an 84-byte IPv4 packet.", 12, 1452),
    "start_offset": ("start", str, "Measurements", "When targets are measured",
                     "Together: every target starts at the beginning of each round. Spread: every target gets its own "
                     "fixed random moment in the round (like SmokePing's offset = random), so not everything is sent "
                     "at once. Single targets can override this.", None, None),
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
    "mtr_rdns": (True, bool, "Traceroutes (mtr)", "Show reverse DNS names",
                 "Look up the hostname (PTR record) of every hop, IPv4 and IPv6, and show it after its address. Names are "
                 "only shown to visitors where the address itself is shown.", None, None),
    "mtr_retention_days": (30, int, "Traceroutes (mtr)", "Keep mtr history (days)", "", 1, 365),

    "alert_email_from": ("", str, "Notifications", "Send emails from",
                         'Sender address, e.g. PingScope <noreply@example.com>. The domain must be verified in Lettermint.',
                         None, None),
    "alert_email_to": ("", str, "Notifications", "Send emails to",
                       "One or more addresses, separated by commas.", None, None),
    "lettermint_token": ("", "secret", "Notifications", "Lettermint API token",
                         "Project API token from lettermint.co (project settings). Stored on the server and never shown "
                         "again; leave empty to keep the current token. The LETTERMINT_TOKEN environment variable, "
                         "if set, takes priority.", None, None),
    "telegram_bot_token": ("", "secret", "Notifications", "Telegram bot token",
                           "Token from @BotFather on Telegram (looks like 123456:ABC-DEF...). Stored on the server and "
                           "never shown again; leave empty to keep the current token. The TELEGRAM_BOT_TOKEN environment "
                           "variable, if set, takes priority.", None, None),
    "telegram_chat_ids": ("", str, "Notifications", "Telegram chat IDs",
                          "Where alerts go by default: one or more chat IDs, separated by commas. Send /start to your bot "
                          "and use Admin → Alerts → Find chat IDs. Per target you can add extra chat IDs, e.g. for the "
                          "customer of that connection.", None, None),
    "pushover_token": ("", "secret", "Notifications", "Pushover application token",
                       "From pushover.net → Your Applications → Create an Application/API Token. Stored on the server "
                       "and never shown again; leave empty to keep the current token. The PUSHOVER_TOKEN environment "
                       "variable, if set, takes priority.", None, None),
    "pushover_users": ("", str, "Notifications", "Pushover user or group keys",
                       "Where Pushover alerts go: one or more user or group keys from pushover.net, separated by commas.",
                       None, None),
    "alert_after_rounds": (3, int, "Notifications", "Rounds in a row before alerting (default)",
                           "A problem must last this many measurement rounds before an alert starts, and the same "
                           "number of good rounds before it counts as resolved. Prevents alerts for a single blip. "
                           "Groups and single targets can override it.",
                           1, 60),
    "alert_recovery": (True, bool, "Notifications", "Notify when resolved",
                       "Also send a message (Telegram and/or email) when a target is fine again.", None, None),
    "alert_reminder_hours": (0, int, "Notifications", "Reminder while still down (hours)",
                             "Send the alert again every this many hours while it lasts. 0 = never.", 0, 168),
}

SECRETS = {k for k, v in SCHEMA.items() if v[1] == "secret"}
# environment variables that override a secret setting
SECRET_ENV = {"lettermint_token": "LETTERMINT_TOKEN", "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
              "pushover_token": "PUSHOVER_TOKEN"}

DEFAULTS = {k: v[0] for k, v in SCHEMA.items()}

# Settings that only accept a fixed set of values: key -> [(value, label), ...]
CHOICES = {
    "theme": [("modern", "Modern"), ("classic", "Classic (SmokePing-style)")],
    "start_offset": [("start", "Together, at the start of each round"), ("random", "Spread over the round (random offset)")],
    "time_zone": [("browser", "Each visitor's own time zone"), ("server", "The server's time zone")],
    "periods": [("1h", "1 hour"), ("3h", "3 hours"), ("6h", "6 hours"), ("12h", "12 hours"), ("1d", "Day"),
                ("7d", "Week"), ("30d", "Month"), ("1y", "Year")],
}


def coerce(key, value):
    """Validate and convert a setting value. Raises ValueError with a readable message."""
    if key not in SCHEMA:
        raise ValueError(f"Unknown setting: {key}")
    default, typ, _, label, _, lo, hi = SCHEMA[key]
    if typ == "multi":  # several of CHOICES, stored comma-separated in the order of CHOICES
        picked = {x.strip().lower() for x in (value if isinstance(value, list) else str(value or "").split(",")) if x.strip()}
        unknown = picked - {c[0] for c in CHOICES[key]}
        if unknown:
            raise ValueError(f"{label}: unknown value {', '.join(sorted(unknown))}")
        if not picked:
            raise ValueError(f"{label}: choose at least one")
        return ",".join(c[0] for c in CHOICES[key] if c[0] in picked)
    if key in CHOICES:
        v = str(value or "").strip().lower()
        if v not in [c[0] for c in CHOICES[key]]:
            raise ValueError(f"{label}: choose one of {', '.join(c[0] for c in CHOICES[key])}")
        return v
    if typ is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if typ == "secret":
        return str(value if value is not None else "").strip()[:500]
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
    if s.get("alert_email_from") and "@" not in s["alert_email_from"]:
        w.append("Notifications: the sender address doesn't look like an email address.")
    for a in [x.strip() for x in s.get("alert_email_to", "").split(",") if x.strip()]:
        if "@" not in a:
            w.append(f"Notifications: '{a}' doesn't look like an email address.")
    worst = max(s["ping_count"], s["dns_count"], s["tcp_count"]) * s["probe_spacing_ms"] + s["timeout_ms"]
    if worst > s["interval"] * 900:
        w.append(
            f"Probes per round × spacing ({worst / 1000:.0f}s) is too long for the {s['interval']}s interval; "
            "rounds may overlap. Lower the counts or spacing, or raise the interval."
        )
    return w


def schema_for_ui():
    return [
        {"key": k, "default": v[0], "type": v[1] if isinstance(v[1], str) else v[1].__name__, "section": v[2], "label": v[3], "help": v[4],
         "min": v[5], "max": v[6], "options": CHOICES.get(k)}
        for k, v in SCHEMA.items()
    ]
