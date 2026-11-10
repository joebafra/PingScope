# Installing PingScope on a server

This guide installs PingScope on a Linux server with its own hostname (for example `ping.example.nl`), a free Let's Encrypt SSL certificate, and an automatic redirect from HTTP to HTTPS.

**Contents**

1. [Requirements](#1-requirements)
2. [Get the code](#2-get-the-code)
3. [Install with Docker (recommended)](#3-install-with-docker-recommended)
4. [HTTPS / SSL certificate (Let's Encrypt)](#4-https--ssl-certificate-lets-encrypt)
   - [Notifications: Telegram, Pushover and email](#4a-notifications-telegram-pushover-and-email)
5. [Updating, backups and useful commands](#5-updating-backups-and-useful-commands)
6. [Install without Docker](#6-install-without-docker)
7. [Using another reverse proxy](#7-using-another-reverse-proxy)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Requirements

- **A Linux server** (VPS or physical) with a public IPv4 address, and ideally a public IPv6 address. Tested on Ubuntu 22.04 / 24.04 and Debian 12.
  - **For IPv6 measurements the server itself needs working IPv6.** Check with `ping -6 -c3 2001:4860:4860::8888`.
- **A hostname for PingScope**, for example `ping.example.nl`, with:
  - an **A record** pointing to the server's IPv4 address, and
  - an **AAAA record** pointing to the server's IPv6 address (recommended).
- **Ports 80 and 443** reachable from the internet. Let's Encrypt validates over port 80. Check the firewall at your hosting provider too.
- **Root or sudo access.**

**Sizing:** 1 vCPU and 1 GB RAM is plenty for a few hundred targets. Disk usage is roughly 2 MB per measured series per two weeks, plus 1 MB per series per year. 300 series need about 1 GB, so plan for at least 5 GB free.

---

## 2. Get the code

PingScope is public on GitHub, so the server can download it without any keys or passwords:

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/joebafra/PingScope.git /opt/pingscope
```

Later updates (`git pull`, `deploy.sh` and the automatic updates in [section 5](#automatic-updates-deploy-from-github)) use the same address and need no login either.

> **Already installed from an SSH clone?** Switch the server to the public address once, and you can remove the deploy key afterwards:
>
> ```bash
> cd /opt/pingscope && sudo git remote set-url origin https://github.com/joebafra/PingScope.git
> ```

### 2.1 Optional: a private fork with an SSH deploy key

Only needed if you run PingScope from **your own private copy** of the repository (for example with local changes). The server then needs permission to download it. The cleanest way is a **deploy key**: an SSH key that gives this one server read-only access to this one repository. In the steps below, replace `<you>/PingScope` with your fork.

#### Create the key on the server

```bash
sudo apt update && sudo apt install -y git openssh-client
sudo mkdir -p /root/.ssh && sudo chmod 700 /root/.ssh
sudo ssh-keygen -t ed25519 -C "pingscope@$(hostname)" -f /root/.ssh/pingscope_deploy -N ""
```

This creates two files:

| File | What it is |
|---|---|
| `/root/.ssh/pingscope_deploy` | The **private** key. It stays on the server; never share it. |
| `/root/.ssh/pingscope_deploy.pub` | The **public** key. This one goes to GitHub. |

#### Tell SSH to use this key for GitHub

Open the SSH config file:

```bash
sudo nano /root/.ssh/config
```

Add these lines, then save:

```
Host github.com
    HostName github.com
    User git
    IdentityFile /root/.ssh/pingscope_deploy
    IdentitiesOnly yes
```

Then set the permissions:

```bash
sudo chmod 600 /root/.ssh/config
```

#### Add the public key to your fork on GitHub

1. Show the public key:
   ```bash
   sudo cat /root/.ssh/pingscope_deploy.pub
   ```
   Copy the whole line. It starts with `ssh-ed25519` and ends with `pingscope@<servername>`.
2. On GitHub, open your fork → **Settings** → **Deploy keys** → **Add deploy key**.
3. Fill in:
   - **Title:** a name for the server, for example `pingscope-server-ams`.
   - **Key:** paste the line.
   - **Allow write access:** leave **unchecked**. The server only needs to read.
4. Click **Add key**.

#### Test the connection

```bash
sudo ssh -T git@github.com
```

The first time, SSH asks whether you trust github.com. Answer **yes**. You should then see:

```
Hi <you>/PingScope! You've successfully authenticated, but GitHub does not provide shell access.
```

If you see `Permission denied (publickey)`:

- Check that you pasted the `.pub` file on GitHub.
- Check that `/root/.ssh/config` points to `/root/.ssh/pingscope_deploy`.

#### Clone your fork

```bash
sudo git clone git@github.com:<you>/PingScope.git /opt/pingscope
```

Later updates with `git pull` use the same key automatically.

> **Alternative: a personal access token over HTTPS**
>
> 1. Create a fine-grained token under GitHub → your profile → **Settings → Developer settings → Personal access tokens**, with **Contents: Read-only** for your fork.
> 2. Run `sudo git clone https://github.com/<you>/PingScope.git /opt/pingscope`.
> 3. Enter your GitHub username as the username and the token as the password.
>
> Tokens expire, and the automatic updates can't type a password, so a deploy key is easier for servers.

---

## 3. Install with Docker (recommended)

### 3.1 Run the installer

```bash
cd /opt/pingscope
sudo ./install.sh
```

The installer will:

1. Install Docker if it isn't there yet (via get.docker.com).
2. Ask for your **hostname** (for example `ping.example.nl`) and an **email address** for Let's Encrypt.
3. Ask for the **port** PingScope listens on (8080 by default). Choose another one if 8080 is already in use on the server, for example by a UniFi controller; the installer warns when a port is taken and suggests a free one. You can also pass it: `sudo PINGSCOPE_PORT=9090 ./install.sh`.
4. Check that the hostname's DNS points to this server, and whether the server has IPv6.
5. Write the settings to `/opt/pingscope/.env`.
6. Open ports 80 and 443 in `ufw` if that firewall is active (without a hostname: the chosen port).
7. Start PingScope and Caddy. Caddy requests the SSL certificate (see [section 4](#4-https--ssl-certificate-lets-encrypt)).
8. Print the address and a one-time **setup code**.

### 3.2 Create your admin account

1. Open `https://<your-hostname>/admin`.
2. Enter the setup code and choose a username and password.

If you missed the setup code, show it again with:

```bash
sudo docker logs pingscope 2>&1 | grep "setup code"
```

### 3.3 Configure PingScope

In the admin panel:

- **Settings → Site:** your own **site title** and welcome text, and whether the dashboard is public.
- **Settings → HTTPS / SSL certificate:** check that the certificate is valid and that HTTP redirects to HTTPS.
- **Import & export:** paste your SmokePing `Targets` config, click **Preview**, then **Import**.
- **IPv6:** find and add IPv6 addresses for targets that are still IPv4-only.

---

## 4. HTTPS / SSL certificate (Let's Encrypt)

PingScope uses **[Caddy](https://caddyserver.com/)** as a web server in front of the app. Caddy takes care of SSL completely:

- It requests a free SSL certificate from **Let's Encrypt** for your hostname.
- It **renews** the certificate automatically, about 30 days before it expires. Certificates are valid for 90 days; you never have to do anything.
- It **redirects** every `http://` request to `https://` with a permanent (308) redirect.
- It sends an **HSTS** header, so browsers keep using HTTPS.

PingScope itself only listens on `127.0.0.1:8080`, so from outside it is reachable only through Caddy and HTTPS.

```
visitor ──http://  port 80 ──▶ Caddy ──308 redirect──▶ https://
visitor ──https:// port 443──▶ Caddy (SSL certificate) ──▶ PingScope (127.0.0.1:8080)
```

The configuration lives in three places:

| File | Contents |
|---|---|
| `/opt/pingscope/.env` | Your hostname and email (written by `install.sh`) |
| `/opt/pingscope/deploy/Caddyfile` | Caddy configuration: HTTPS, redirect, proxy to PingScope |
| `/opt/pingscope/data/caddy/` | Certificates and the Let's Encrypt account. Keep this folder. |

The SSL-related settings in `.env`:

```ini
PINGSCOPE_DOMAIN=ping.example.nl     # your hostname; several: "ping.example.nl, status.example.nl"
ACME_EMAIL=you@example.nl            # Let's Encrypt sends expiry warnings here (optional)
COMPOSE_PROFILES=https               # "https" starts Caddy; empty = no HTTPS
PINGSCOPE_LISTEN=127.0.0.1           # PingScope only reachable via Caddy
```

### 4.1 Before the certificate can be issued

1. **DNS points to the server.** Check from any computer:
   ```bash
   dig +short ping.example.nl A
   dig +short ping.example.nl AAAA
   ```
   Both must return this server's address. A new DNS record can take a few minutes to become visible.
2. **Port 80 and 443 are open.** With ufw:
   ```bash
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   ```
   Also check the firewall or security group at your hosting provider.

### 4.2 Enabling HTTPS later, or changing the hostname

1. Point the DNS of the (new) hostname to the server.
2. Do one of these:
   - Run `sudo ./install.sh` again and enter the hostname.
   - Or edit `/opt/pingscope/.env` yourself: set `PINGSCOPE_DOMAIN`, `COMPOSE_PROFILES=https` and `PINGSCOPE_LISTEN=127.0.0.1`.
3. Apply the change:
   ```bash
   cd /opt/pingscope
   sudo docker compose up -d
   ```

Caddy requests the new certificate within seconds.

### 4.3 Checking the certificate

- **In PingScope:** **Admin → Settings → HTTPS / SSL certificate** shows, per hostname:
  - who issued the certificate;
  - when it expires;
  - whether HTTP redirects to HTTPS.
- **On the server:**
  ```bash
  curl -I http://ping.example.nl                 # expect: 308 Permanent Redirect, Location: https://...
  curl -sI https://ping.example.nl | head -1      # expect: HTTP/2 200
  sudo docker logs pingscope-caddy 2>&1 | grep -i -E "certificate|error"
  ```

### 4.4 Using your own certificate (optional)

If you already have a certificate from another provider (for example a wildcard):

1. Copy `fullchain.pem` and `privkey.pem` to `/opt/pingscope/data/caddy/data/certs/`.
2. In `deploy/Caddyfile`, add this line inside the site block (below `encode zstd gzip`):
   ```
   tls /data/certs/fullchain.pem /data/certs/privkey.pem
   ```
3. Restart Caddy:
   ```bash
   sudo docker compose restart caddy
   ```

You must renew this certificate yourself.

### 4.5 Experimenting? Use Let's Encrypt staging (optional)

Let's Encrypt limits how many certificates you can request for the same hostname (5 per week). While testing, add this line to the `{ }` block at the top of `deploy/Caddyfile`:

```
acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
```

Browsers will show a warning with staging certificates. Remove the line when everything works, delete `data/caddy/data/caddy/certificates`, and run `sudo docker compose restart caddy`.

### 4.6 Without a hostname (no HTTPS)

Run the installer and leave the hostname empty. PingScope then listens on `http://<server-ip>:8080` (or the port you chose) without HTTPS. Only use this on a trusted network.

---

## 4a. Notifications: Telegram, Pushover and email

PingScope can alert you when targets have packet loss or are down, by **Telegram**, **[Pushover](https://pushover.net)** and/or **email** (via [Lettermint](https://lettermint.co)). Set up one or more.

### Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy its token.
2. Open your bot in Telegram and send it **/start** (or add the bot to a group and send a message there).
3. **Admin → Settings → Notifications:** paste the *Telegram bot token* and save.
4. **Admin → Alerts → Find chat IDs:** shows the chats that wrote to your bot. Click **Save as contact** to store one under **Telegram contacts** with a name of your choice. Tick **Gets all alerts** for yourself (or put the ID in *Settings → Notifications → Telegram chat IDs*).
5. **Admin → Alerts → Send test alert:** sends a sample alert, marked TEST, exactly as a real one looks, to every admin channel (Telegram, Pushover and email). Each contact also has its own *Test* button.

**Alerts for a customer:** save the customer as a Telegram contact (they first send /start to your bot, or add the bot to a group), then in the target's edit dialog tick them under *Also send alerts for this target to*. Unsaved chat IDs can be typed in the field below. Those chats get messages about that target only, without group names. Notifications must be on for the target's group. *Send test alert* in the dialog checks it. The same works for a whole group: *Edit group → Also send alerts for this group to* (also when the group follows its parent's notification settings). Those chats get messages about every target in that group and its subgroups; on the targets they show as "via group". Deleting a contact also removes it from its targets and groups.

### Pushover

1. Log in at [pushover.net](https://pushover.net) and copy your **User Key** (top right on the dashboard). For a team, create a delivery group and use its group key.
2. Under **Your Applications → Create an Application/API Token**, create an application (for example "PingScope") and copy its **API Token**.
3. **Admin → Settings → Notifications:** paste the *Pushover application token* and enter one or more *Pushover user or group keys* (comma-separated), then save.
4. **Admin → Alerts → Send test alert** checks it: the Pushover app shows a message marked TEST.

Problems arrive with normal priority and a link to the graphs (when `PINGSCOPE_DOMAIN` is set); messages that only say something is fine again arrive quietly.

### Email (Lettermint)

1. **In Lettermint:** add and verify your sending domain (for example `example.nl`), then create a **Project API token** in the project settings.
2. **In PingScope → Admin → Settings → Notifications:**
   - *Send emails from*: e.g. `PingScope <noreply@yourdomain.nl>` (the domain must be verified in Lettermint)
   - *Send emails to*: one or more addresses, separated by commas
   - *Lettermint API token*: paste the token. It is stored on the server and never shown again.
   - Optionally change *Rounds in a row before alerting* (default 3), *Email when resolved* and the reminder interval.
3. **Admin → Alerts → Send test email** to check that everything works.

### Turn alerts on per group

Admin → Targets → select a group → *Edit group* → *Notifications: On*. Set the packet-loss percentage (for example 10%), whether to alert when a target is down (no replies at all), and whether to send a Telegram message and/or an email. Subgroups set to *Same as the parent group* follow the group above them. *Rounds in a row before alerting* can be set per group (empty = the default in Settings) and overridden per target in its edit dialog, e.g. 1 for a critical link or 5 for a flaky one.

While you edit the group, a **preview** shows how many targets would have alerted with these settings over the last 24 hours, 7 or 14 days, how many alerts and roughly how many messages per day. It lists the targets that would alert, each with a **Skip** button.

**Skip single targets:** a target with *No alerts for this target* (in its edit dialog, via the 🔕 Skip button in the preview, or with the bulk buttons *🔕 No alerts* / *🔔 Alerts on* after selecting targets) never alerts, even when its group has notifications on. Skipped targets show a 🔕 in the target list.

All alerts that start or end in the same measurement round are combined into one message, so a large outage sends one message instead of hundreds. Active alerts and the history of the last 90 days are listed under **Admin → Alerts**.

Instead of entering the tokens in the admin panel you can put `TELEGRAM_BOT_TOKEN=...`, `PUSHOVER_TOKEN=...` and/or `LETTERMINT_TOKEN=...` in `.env` and run `docker compose up -d`. Set `PINGSCOPE_DOMAIN` so messages contain links to the graphs.

## 5. Updating, backups and useful commands

### Automatic updates (deploy from GitHub)

When something new is pushed to the `main` branch on GitHub, the server installs it automatically. The installer asks whether to enable this.

A systemd timer runs `deploy.sh` every 5 minutes. The script:

1. Checks GitHub for new commits on `main`. If there are none, it stops right away.
2. Makes a **backup of the database** in `data/backups/`. The last 5 are kept.
3. Pulls the new code and **rebuilds/restarts** PingScope.
4. Checks that PingScope responds again (health check, up to 2 minutes).
5. If the new version doesn't start, it **rolls back** to the previous version automatically. It then skips that broken version until a newer commit arrives.

Your settings (`.env`) and data (`data/`) are never touched by git.

The public repository needs no login. For a private fork, the server must read it with a deploy key ([section 2.1](#21-optional-a-private-fork-with-an-ssh-deploy-key)), because the timer can't type a password.

```bash
sudo ./deploy.sh --status                         # running version vs. available version
sudo ./deploy.sh                                  # deploy now instead of waiting for the timer
sudo ./deploy.sh --force                          # rebuild/restart even without changes, or retry a failed version
sudo journalctl -u pingscope-deploy -n 50         # deploy log
sudo systemctl list-timers pingscope-deploy.timer # when the next check runs
```

To enable it later:

```bash
cd /opt/pingscope
sudo cp deploy/pingscope-deploy.service deploy/pingscope-deploy.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pingscope-deploy.timer
```

To turn it off:

```bash
sudo systemctl disable --now pingscope-deploy.timer
```

You can also run `sudo ./install.sh` again. `deploy.sh` works the same way for the install without Docker ([section 6](#6-install-without-docker)).

> **Tip:** to try changes before they go live, work on a separate branch and merge into `main` when you're ready. Only `main` is deployed. To deploy another branch on a test server, set `Environment=PINGSCOPE_BRANCH=<branch>` in the service file.

### Updating manually

If you don't use automatic updates:

```bash
cd /opt/pingscope
sudo ./deploy.sh
```

This includes the backup and rollback. Prefer it over a plain `git pull`: after `git pull` alone, the new code is on disk but the running container is still the old one. `deploy.sh` notices that and rebuilds. Without the script:

```bash
sudo git pull && sudo docker compose up -d --build
```

Your data in `/opt/pingscope/data` (database and certificates) is kept.

### Backups

All PingScope data lives in one SQLite file: targets, settings, users, measurements and traceroutes. For a consistent copy while PingScope is running:

```bash
sudo docker exec pingscope python -m pingscope backup /data/backup-$(date +%F).db
# the file appears in /opt/pingscope/data/
```

To export only the configuration (groups, targets, settings) as JSON, use **Admin → Import & export → Download JSON export**.

### Useful commands

```bash
sudo docker compose ps                    # status
sudo docker logs -f pingscope             # PingScope log
sudo docker logs -f pingscope-caddy       # HTTPS / certificate log
sudo docker compose restart pingscope     # restart PingScope
sudo docker exec -it pingscope python -m pingscope create-admin   # extra admin from the shell
```

---

## 6. Install without Docker

These steps are for Ubuntu or Debian.

### 6.1 Packages, user and code

```bash
sudo apt update
sudo apt install -y git python3 python3-venv fping mtr-tiny
sudo useradd --system --home /var/lib/pingscope --create-home --shell /usr/sbin/nologin pingscope
sudo git clone https://github.com/joebafra/PingScope.git /opt/pingscope
cd /opt/pingscope
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
```

### 6.2 Service

```bash
sudo cp deploy/pingscope.service /etc/systemd/system/
sudo nano /etc/systemd/system/pingscope.service
```

In the editor, set `PINGSCOPE_DOMAIN` to your hostname. It is only used for the HTTPS status page. Then start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pingscope
sudo journalctl -u pingscope | grep "setup code"
```

PingScope now listens on `127.0.0.1:8080`.

### 6.3 HTTPS with Caddy

Install Caddy from its official repository:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Replace the contents of `/etc/caddy/Caddyfile` with the following, using your own hostname and email:

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

Caddy fetches the Let's Encrypt certificate, renews it automatically, and redirects HTTP to HTTPS, exactly as in the Docker setup.

### 6.4 Updating

Run `sudo ./deploy.sh` (with backup and rollback). Or enable automatic updates with the timer as described in [section 5](#automatic-updates-deploy-from-github). The deploy script detects the installation without Docker by itself.

---

## 7. Using another reverse proxy

You can also use nginx, Apache, Plesk or similar instead of Caddy. Proxy to `http://127.0.0.1:8080` and pass the usual forwarding headers. For example, with nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Certificates are then handled by that web server (for example with certbot). If the proxy runs on another machine, set `PINGSCOPE_TRUSTED_PROXIES` to its IP address.

---

## 8. Troubleshooting

| Problem | What to check |
|---|---|
| Browser warning / no certificate | **Admin → Settings → HTTPS** and `sudo docker logs pingscope-caddy`. Usually the DNS record doesn't point to the server yet, or port 80 is blocked (also check the hosting provider's firewall). |
| "too many certificates already issued" | Let's Encrypt rate limit: wait a few days, or use staging while testing ([4.5](#45-experimenting-use-lets-encrypt-staging-optional)). |
| `git clone`: Permission denied (publickey) | Only for a private fork over SSH: the deploy key isn't added on GitHub, or `/root/.ssh/config` doesn't point to it. Test with `sudo ssh -T git@github.com`. For the public repository, clone over HTTPS: `https://github.com/joebafra/PingScope.git`. |
| A new version on GitHub isn't installed | `sudo ./deploy.sh --status` and `sudo journalctl -u pingscope-deploy -n 50`. If that version failed before, it is skipped until a newer commit (or run `sudo ./deploy.sh --force`). |
| All IPv6 graphs are down | Does the server have IPv6? `ping -6 2001:4860:4860::8888` |
| Port 8080 is already in use (for example by a UniFi controller), or PingScope doesn't start | PingScope uses the server's network directly, so its port must be free on the server, with or without HTTPS. Run `sudo ./install.sh` again and choose another port (it warns when a port is taken), or set `PINGSCOPE_PORT=9090` in `.env` and run `sudo docker compose up -d`. Without HTTPS, open the new port in the firewall (`sudo ufw allow 9090/tcp`). |
| Ping targets all down, DNS targets fine | The container needs `NET_RAW` (already in docker-compose.yml). Check `sudo docker logs pingscope`. |
| Forgot admin password | `sudo docker exec -it pingscope python -m pingscope create-admin` creates a new admin. |
| "Rounds may overlap" warning | Lower the probes per round or the probe spacing in Settings, or raise the interval. |
