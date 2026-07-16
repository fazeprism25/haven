# Deploying Haven to Alibaba Cloud

This deploys Haven's existing FastAPI server (`obsidian.server.main:app`,
started the same way as local dev — `uvicorn obsidian.server.main:app`) to a
single Alibaba Cloud VM, unmodified. Nothing in `obsidian/memory_engine/`,
`obsidian/ontology/`, `obsidian/manager_ai/`, or `benchmarks/` changes —
this only adds production process management (systemd), a reverse proxy
(nginx), and a firewall in front of the same app.

## 1. Which Alibaba Cloud service, and why

Haven's server is a **single Python process** with an in-memory concept
graph and a process-wide write lock (see `obsidian/server/main.py`'s
`_write_lock` docstring — this is intentional, not a limitation to work
around), backed by **plain files on local disk** (the Markdown vault,
concept notes, checkpoints, write traces). There is no database to
provision and nothing to horizontally scale — the whole app is "one VM,
one disk, one process."

That rules out the serverless/managed options:

| Option | Why not |
|---|---|
| Function Compute (FC) | No persistent local filesystem between invocations — Haven's vault *is* a local directory tree; FC would need it moved to OSS/NAS, which is exactly the backend redesign this task says not to do. |
| Serverless App Engine (SAE) / Container Service (ACK) | Built for scaling stateless containers behind a load balancer. Haven is deliberately single-process/single-user (its own code comments say so); a container orchestrator adds real operational complexity (image builds, registry, ingress) for zero benefit here. |
| RDS / PolarDB | Haven has no database — it's Markdown + YAML on disk by design. Nothing to provision. |

That leaves a plain VM, and Alibaba Cloud has two ways to get one:

- **ECS (Elastic Compute Service)**, pay-as-you-go: you provision compute,
  a system disk, a public EIP, and a security group as separate billable
  items, and manage all of it yourself.
- **Simple Application Server (SWAS / "轻量应用服务器")**: the same
  underlying ECS compute, pre-packaged as a single VM with a fixed monthly
  price that **already bundles the disk, a public IP, and a data-transfer
  allowance**, plus a simplified firewall panel and one-click snapshots.

**Recommendation: Simple Application Server.** It's still literally an ECS
instance — Alibaba positions it as ECS's "single server" tier for exactly
this shape of workload (one app, one VM, no need to hand-assemble a
security group and an EIP separately) — so it satisfies "prefer ECS unless
there's a clearly better option" while removing the parts of classic ECS
that don't earn their complexity for a one-VM hackathon deployment: separate
bandwidth billing, a separate EIP to provision/release, and a security
group to hand-write from scratch. If you'd rather use classic pay-as-you-go
ECS instead (e.g. because SWAS isn't available in your account/region),
everything from provisioning onward in this guide is identical — you're
just SSHing into a VM either way. Only the console screens for creating it
and the exact firewall UI differ.

## 2. Sizing and cost

Simple Application Server Linux plans (published pricing, confirm exact
figures on the [SWAS pricing page](https://www.alibabacloud.com/en/product/swas/pricing)
before purchase — prices vary by region and change over time):

| Plan | vCPU | RAM | SSD | Bundled transfer | Price |
|---|---|---|---|---|---|
| Entry | 1 | 0.5 GB | 20 GB | 1 TB/mo | $3.50/mo |
| **Recommended** | **1** | **2 GB** | **40 GB** | **1.5 TB/mo** | **$9.00/mo** |
| Next tier | 2 | 2 GB | 60 GB | 2 TB/mo | $15.00/mo |

**Pick the 1 vCPU / 2 GB plan.** Haven's process itself is light (FastAPI +
uvicorn + a Markdown/YAML vault measured in single-digit MB for a demo-sized
vault), but 2 GB gives comfortable headroom for the OS, nginx, and installing
`openai`/`pydantic`/etc. in a venv without swap thrashing. The 0.5 GB entry
plan can work but leaves very little slack during `pip install` or if
Manager AI's LLM client briefly buffers a large conversation payload — not
worth saving $5.50/month on a submission you want to stay up reliably.

**Cost vs. the $40 coupon:** at $9.00/month, $40 covers **~4.4 months** —
comfortably past any hackathon judging window. If you want maximum runway
instead, the $4.50/month (1 GB RAM) plan stretches the same coupon to
~8.8 months; it's a reasonable fallback if 2 GB isn't available in your
region at that price. Either way, no other billable resource is needed —
SWAS's price already includes the disk, the public IP, and the bandwidth
allowance, so there's no separate EIP or traffic bill to watch.

**Region:** pick whichever region is (a) covered by your coupon and (b)
closest to you/your judges for latency — Haven has no data-residency
requirement to satisfy here.

**OS image:** Ubuntu 22.04 LTS (plain OS image, not one of SWAS's
pre-bundled app images). Ubuntu 22.04 ships Python 3.10, matching Haven's
`requires-python = ">=3.10,<4.0"`.

## 3. Create the instance (console)

1. Alibaba Cloud Console → **Simple Application Server** → **Create Instance**.
2. Region: your choice (see above). Image: **Ubuntu 22.04**.
3. Plan: **1 vCPU / 2 GB / 40 GB SSD** (or your chosen tier from §2).
4. Set the root/instance password (or upload an SSH key if the console
   offers it) — you'll need this to SSH in for step 4.
5. Confirm and pay (this is what draws down the coupon).
6. Once running, open the instance's **Firewall** tab and confirm rules
   exist for **22 (SSH)**, **80 (HTTP)**, and **443 (HTTPS)** from
   `0.0.0.0/0` — SWAS instances usually pre-open 22/80/443 on the plain
   Ubuntu image, but check rather than assume. (If you went the classic-ECS
   route instead: create these same three rules in that instance's
   **Security Group**, and separately allocate + bind an EIP.)
7. Note the instance's **public IP** — you'll need it for every step below.

## 4. Provision the server

Everything after this point is scripted — `provision.sh` is idempotent, so
re-running it after a `git pull` is the normal way to pick up new code.

```bash
ssh root@<public-ip>

# on the instance:
git clone --depth 1 https://github.com/fazeprism25/haven.git /tmp/haven-bootstrap
sudo bash /tmp/haven-bootstrap/deploy/alibaba-cloud/provision.sh
```

`provision.sh` (see that file for the full annotated script) does, in order:

1. Installs `python3`, `nginx`, `certbot`, `ufw`, and `apache2-utils` (for
   `htpasswd`).
2. Creates a dedicated, unprivileged `haven` system user — the app never
   runs as root.
3. Clones Haven to `/opt/haven` as that user (or `git pull`s if already
   present).
4. Creates a venv at `/opt/haven/.venv` and installs
   `obsidian/server/requirements.txt` — the same file local dev uses, plus
   `openai` (added to that file for this task — Manager AI's real
   extraction calls need it; it's already imported behind a
   try/except everywhere in the codebase, so nothing else changes).
5. Copies `config/manager_ai.env.example` → `config/manager_ai.env` if it
   doesn't exist yet and tells you to fill in `MANAGER_AI_API_KEY` — do
   that next:
   ```bash
   sudo -u haven nano /opt/haven/config/manager_ai.env
   # set MANAGER_AI_API_KEY=<your Qwen/DashScope key>
   sudo systemctl restart haven
   ```
   The demo endpoints (`/api/v1/dev/seed_demo`, the dashboard's "Import
   Demo Data") work with no key at all, same as local dev — only real
   `/api/v1/memory`-family calls need this.
6. Installs and starts `haven.service` (systemd) — see that file for why it
   deliberately runs a single uvicorn worker, no `--reload`, on
   `127.0.0.1:8765` only (not exposed directly; nginx is the only thing
   allowed to reach it from outside).
7. Installs the nginx reverse-proxy config, prompts you once to set an
   HTTP Basic Auth username/password (see §5 for why), and reloads nginx.
8. Opens 22/80/443 in `ufw` (belt-and-suspenders with the console firewall
   rules from step 3).

## 5. Why Basic Auth is here even though the app code wasn't touched

Haven's own server README says it plainly: *"This server is still intended
to run locally, for a single user, with no authentication — if it's ever
exposed beyond localhost, authentication would need to be added first."*
Deploying it to a public IP is exactly that case. Adding auth **inside**
`obsidian/server/main.py` would be exactly the kind of business-logic
change this task says not to make — so instead, `nginx.haven.conf` puts
HTTP Basic Auth in front of the whole app at the reverse-proxy layer, with
one deliberate exception: `GET /api/v1/health` stays open, so you (or a
judge) can prove the deployment is live with a single unauthenticated
`curl` without exposing any vault data or write access.

## 6. Verify

```bash
# No auth needed:
curl -s http://<public-ip>/api/v1/health
# {"status":"ok"}

# Needs the Basic Auth credentials from provisioning:
curl -s -u <user>:<password> http://<public-ip>/dashboard | head -5
```

Also check the service directly on the box if anything looks wrong:

```bash
systemctl status haven
journalctl -u haven -f          # live logs
sudo nginx -t                   # validate the nginx config
```

## 7. Optional: a real domain + TLS

If you point a domain's A record at the instance's public IP:

```bash
sudo certbot --nginx -d your.domain.com
```

Certbot edits `/etc/nginx/sites-available/haven.conf` in place to add a
`listen 443 ssl` block and an HTTP→HTTPS redirect, and sets up auto-renewal.
Without a domain, the deployment stays on plain HTTP — acceptable for a
short-lived hackathon demo behind Basic Auth, but traffic (including the
Basic Auth credentials themselves) is unencrypted in that case, so don't
reuse those credentials anywhere sensitive.

## 8. Updating after a new commit

```bash
ssh root@<public-ip>
sudo bash -c 'cd /opt/haven && git pull --ff-only'
sudo -u haven /opt/haven/.venv/bin/pip install -q -r /opt/haven/obsidian/server/requirements.txt
sudo systemctl restart haven
```

(Or just re-run `provision.sh` — it's idempotent and does all of the above
plus re-checks nginx/ufw.)

## 9. Backing up the vault

`haven_data/` (or wherever `config/spaces.json` points an active Memory
Space's directories) on the instance **is** the user's actual memory data —
it's the one thing here that isn't reproducible from git. SWAS's console
has a one-click **Snapshot** feature under the instance's **Disks** tab;
take one after your first successful demo import, and before any risky
change (OS upgrade, disk resize).

## 10. What "proof of deployment" should capture

Once §6 passes, for the hackathon submission capture:

- The `curl http://<public-ip>/api/v1/health` transcript (status `200`,
  body `{"status":"ok"}`).
- A screenshot or the JSON from `GET /api/v1/dashboard` (via `curl -u`)
  showing real data, or a screenshot of `/dashboard` in a browser.
- The Alibaba Cloud console screen showing the running instance (region,
  spec, public IP redacted or not, your call).

This is added to the root `README.md`'s new "Deployment" section — see
that section for where it links back here.

## 11. Hosted demo Memory Spaces

`haven.service` sets `HAVEN_HOSTED_DEMO=true` (see that file), which changes
first-startup behavior specifically for this deployment: instead of
synthesizing one empty "Default" Memory Space, Haven bootstraps two
pre-populated ones — **Haven Development** (active by default) and
**Personal AI Research** — each already seeded with its own bundled demo
dataset (`obsidian/server/main.py`'s `_bootstrap_hosted_demo_registry`, the
same deterministic no-API-key seeding `POST /api/v1/dev/seed_demo` already
uses). A judge opening `/dashboard` after Basic Auth sees a fully populated
instance immediately — no folder to choose, no Import Demo Data click
needed — and can switch between the two spaces from the Settings section.

This only runs once: the very first startup with no `config/spaces.json` on
disk yet. Every later restart (including `systemctl restart haven` after a
`git pull`) finds that file already there and skips straight past it, so
demo data is never re-seeded or duplicated. Local dev (`uvicorn` on
`127.0.0.1`/`localhost`, no `HAVEN_HOSTED_DEMO` set) is completely
unaffected — it keeps the existing "pick a folder / import a vault"
workflow exactly as it was.

If you ever need to wipe this instance back to a truly fresh state (e.g.
testing the bootstrap itself again), stop the service, delete both
`config/spaces.json` and `haven_data/demo_spaces/`, and restart:

```bash
sudo systemctl stop haven
sudo -u haven rm -rf /opt/haven/config/spaces.json /opt/haven/haven_data/demo_spaces
sudo systemctl start haven
```
