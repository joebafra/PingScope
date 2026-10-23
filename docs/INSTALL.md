# Installing PingScope on a server

This guide installs PingScope on a Linux server with its own hostname (for example `ping.example.nl`), a free Let's Encrypt certificate, and an automatic redirect from HTTP to HTTPS.

There are two ways to install:

- **Option A: Docker (recommended).** One script sets up everything, including HTTPS.
- **Option B: without Docker.** Python + systemd + Caddy installed directly on the server.

## Requirements

- A Linux server (VPS or physical) with a public IPv4 address, and ideally a public IPv6 address. Tested on Ubuntu 22.04 / 24.04 and Debian 12.
  - **For IPv6 measurements the server itself needs working IPv6.** Check with `ping -6 -c3 2001:4860:4860::8888`.
- A hostname for PingScope, for example `ping.example.nl`, with:
  - an **A record** pointing to the server's IPv4 address, and
  - an **AAAA record** pointing to the server's IPv6 address (recommended).
- Ports **80** and **443** reachable from the internet (Let's Encrypt validates over port 80).
- Root or sudo access.

Sizing: 1 vCPU and 1 GB RAM is plenty for a few hundred targets. Disk usage is roughly 2 MB per measured series per two weeks plus 1 MB per series per year. 300 series need about 1 GB, so plan for at least 5 GB free.

---

## Option A: Docker (recommended)

### 1. Get the code

The repository is private, so log in to GitHub on the server first. Two ways to do that:

- **Personal access token:** create one at GitHub → Settings → Developer settings → Fine-grained tokens, with read access to *Contents* of `internetcreators/PingScope`. Then run:
  ```bash
  sudo apt update && sudo apt install -y git
  git clone https://github.com/internetcreators/PingScope.git /opt/pingscope
  # username: your GitHub username, password: the token
  ```
- **Deploy key:** add the server's SSH key as a read-only deploy key on the repository. Then run:
  ```bash
  git clone git@github.com:internetcreators/PingScope.git /opt/pingscope
  ```

### 2. Run the installer

```bash
cd /opt/pingscope
sudo ./install.sh
```

The installer will:

1. Install Docker if it isn't there yet (via get.docker.com).
2. Ask for your **hostname** and an **email address** for Let's Encrypt.
3. Check that the hostname's DNS points to this server, and whether the server has IPv6.
4. Write the settings to `.env`, open ports 80/443 in `ufw` if that firewall is active, and start PingScope and Caddy.
5. Print the address and a one-time **setup code**.

### 3. Create your admin account

1. Open `https://<your-hostname>/admin`.
2. Enter the setup code and choose a username and password.

If you missed the setup code, show it again with:

```bash
docker logs pingscope 2>&1 | grep "setup code"
```

### 4. Configure

In the admin panel:

- **Settings → Site:** set your own **site title** and welcome text, and choose whether the dashboard is public.
- **Import & export:** paste your SmokePing `Targets` config. Click **Preview**, then **Import**.
- **IPv6:** find and add IPv6 addresses for targets that are still IPv4-only.

That's it. HTTP requests are redirected to HTTPS, and the certificate renews automatically.

### Changing the hostname later

Edit `PINGSCOPE_DOMAIN` (and `ACME_EMAIL`) in `/opt/pingscope/.env`, make sure DNS points to the server, then run:

```bash
cd /opt/pingscope && sudo docker compose up -d
```

Caddy requests a certificate for the new name automatically. You can list several names, separated by commas: `PINGSCOPE_DOMAIN=ping.example.nl, status.example.nl`.

### Updating

```bash
cd /opt/pingscope
sudo git pull
sudo docker compose up -d --build
```

Your data in `/opt/pingscope/data` is kept.

### Backups

All data (targets, settings, users, measurements, traceroutes) lives in one SQLite file. For a consistent copy while PingScope is running:

```bash
sudo docker exec pingscope python -m pingscope backup /data/backup-$(date +%F).db
# the file appears in /opt/pingscope/data/
```

To export only the configuration (groups, targets, settings) as JSON, use **Admin → Import & export → Download JSON export**.

### Useful commands

```bash
sudo docker compose ps                  # status
sudo docker logs -f pingscope           # PingScope log
sudo docker logs -f pingscope-caddy     # HTTPS / certificate log
sudo docker compose restart pingscope   # restart
sudo docker exec -it pingscope python -m pingscope create-admin   # extra admin from the shell
```

### Without a hostname (no HTTPS)

Run the installer and leave the hostname empty. PingScope then listens on `http://<server-ip>:8080` without HTTPS. Only use this on a trusted network.

---

## Option B: without Docker

These steps are for Ubuntu or Debian.

### 1. Packages and user

```bash
sudo apt update
sudo apt install -y git python3 python3-venv fping mtr-tiny
sudo useradd --system --home /var/lib/pingscope --create-home --shell /usr/sbin/nologin pingscope
sudo git clone https://github.com/internetcreators/PingScope.git /opt/pingscope
cd /opt/pingscope
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
```

### 2. Service

```bash
sudo cp deploy/pingscope.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pingscope
sudo journalctl -u pingscope | grep "setup code"
```

PingScope now listens on `127.0.0.1:8080`.

### 3. HTTPS with Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Put this in `/etc/caddy/Caddyfile`, replacing the hostname and email:

```
{
	email you@example.nl
}

ping.example.nl {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8080
	header Strict-Transport-Security "max-age=31536000"
}
```

Then reload Caddy:

```bash
sudo systemctl reload caddy
```

Caddy fetches the Let's Encrypt certificate, renews it, and redirects HTTP to HTTPS.

### Updating (Option B)

```bash
cd /opt/pingscope && sudo git pull && sudo venv/bin/pip install -r requirements.txt && sudo systemctl restart pingscope
```

---

## Using another reverse proxy (nginx, Apache, Plesk, …)

Proxy to `http://127.0.0.1:8080` and pass the usual forwarding headers. For example, with nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

If the proxy runs on another machine, set `PINGSCOPE_TRUSTED_PROXIES` to its IP address.

## Troubleshooting

| Problem | Check |
|---|---|
| No certificate / browser warning | `docker logs pingscope-caddy`. Usually the DNS record doesn't point to the server yet, or port 80 is blocked. |
| All IPv6 graphs are down | Does the server have IPv6? `ping -6 2001:4860:4860::8888` |
| Ping targets all down, DNS targets fine | The container needs `NET_RAW` (already in docker-compose.yml). Check `docker logs pingscope`. |
| Forgot admin password | `docker exec -it pingscope python -m pingscope create-admin` creates a new admin. |
| "Rounds may overlap" warning | Lower the probes per round or the probe spacing in Settings, or raise the interval. |
