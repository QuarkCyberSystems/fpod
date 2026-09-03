# fpod

Manage [Frappe](https://frappeframework.com/) benches on **rootless Podman**.

One CLI to spin up isolated Frappe dev environments, each at `<name>.localhost`,
backed by a single shared MariaDB + Redis + Traefik stack.

For the full architecture and design notes, see [`PLAN.md`](./PLAN.md).

---

## Why fpod

[Frappe-Manager](https://github.com/rtCamp/Frappe-Manager) (`fm`) by **rtCamp**
is the established single-CLI way to run Frappe environments — but it's built on
**Docker**. On hosts where Docker is awkward or unwanted — Steam Deck / SteamOS,
immutable Arch/Fedora, locked-down workstations — **rootless Podman** is the
native container runtime, and Docker-first tooling fights the grain (daemon,
root socket, package availability).

fpod is a Podman-native take on the same idea: one CLI, multiple isolated
benches, shared backing services, and hostname routing — with none of the
Docker assumptions. It deliberately stays small and **dev-focused** rather than
matching fm feature-for-feature. If you're on Docker and want a mature,
production-capable tool, use fm; if you're on rootless Podman and want a lean
dev setup, fpod is built for that.

---

## What you get

- `fpod create demo` — provisions a bench, creates `demo.localhost`, ready to log in.
- `fpod create sandbox` — second bench, independent DB, runs alongside the first.
- `fpod shell demo` — drops you into the bench container.
- `fpod code demo` — opens VS Code attached to the container.
- `fpod install-app demo erpnext` — clones ERPNext into the bench, installs it on the site.
- `fpod backup demo` — DB dump + files tarballs copied out to `~/.fpod/backups/`.
- `fpod doctor` — health check across host, stack, and each bench.

Plus shared services every bench gets for free:

- **Mailpit** at `http://mail.localhost` — catches all outbound mail for testing.
- **Adminer** at `http://db.localhost` — web DB browser.
- **Traefik dashboard** at `http://traefik.localhost/dashboard/` — see routing in real time.

---

## fpod vs Frappe-Manager

fpod owes its shape to [Frappe-Manager](https://github.com/rtCamp/Frappe-Manager).
The differences are deliberate, not omissions of ambition:

| | **fpod** | **Frappe-Manager (fm)** |
|---|---|---|
| Container runtime | rootless **Podman** | **Docker** |
| Orchestration | `podman-compose` | `docker compose` |
| Focus | development only | development **and** production |
| Reverse proxy | Traefik (label auto-discovery) | nginx (templated configs) |
| Multi-bench | yes | yes |
| Mailpit + Adminer | yes | yes |
| HTTPS / Let's Encrypt | no (localhost only) | yes (`fm ssl`) |
| Production mode (gunicorn/supervisor) | no | yes |
| Maturity | new, minimal surface | mature, many releases |

**When to use which:** on Docker, or if you need production mode / TLS / a
battle-tested tool — use **fm**. On rootless Podman and you just want fast,
disposable dev benches — use **fpod**.

---

## Requirements

- Linux with rootless Podman (5.x tested). Other container runtimes are not supported.
- Python 3.11+.
- ~5 GB free disk for shared service images, plus ~1 GB per bench.

---

## Install

### Option A — install script (recommended)

Creates a venv, installs fpod editable, and symlinks `fpod` onto your PATH:

```bash
git clone https://github.com/QuarkCyberSystems/fpod.git ~/fpod
cd ~/fpod
./install.sh
```

The script gates on Python 3.11+, is idempotent (safe to re-run after `git pull`),
and warns if `~/.local/bin` isn't on your PATH. Override with `PYTHON=…` or
`FPOD_BIN_DIR=…`.

### Option B — pipx (no repo checkout to keep around)

pipx installs into an isolated venv and puts the `fpod` entry point on your PATH
automatically:

```bash
pipx install git+https://github.com/QuarkCyberSystems/fpod.git
# or from a local clone:
pipx install .
```

If you don't have pipx:

```bash
python3 -m pip install --user pipx && python3 -m pipx ensurepath
```

> Use Option A if you intend to hack on fpod (editable install); Option B for a
> plain "I just want the tool" install.

---

## First-time setup

```bash
fpod init                # defaults to port 80
```

If you don't have `net.ipv4.ip_unprivileged_port_start <= 80`, `init` will tell
you the exact `sysctl` to run, **or** rerun with a high port:

```bash
fpod init --port 8080
```

`init` is idempotent — safe to re-run.

For services to survive logout (so benches keep running on reboot):

```bash
sudo loginctl enable-linger $USER
```

---

## Installing on another machine

fpod runs on any Linux host with **rootless Podman 5.x**, **Python 3.11+**, and
**git**. Nothing is shared between machines — each host gets its own `~/.fpod/`
(config, benches, backups), its own `fpod-net` network, and its own services pod.

```bash
# 1. clone (repo is private — auth via gh / SSH key / PAT)
git clone https://github.com/QuarkCyberSystems/fpod.git ~/fpod
cd ~/fpod

# 2. install (Option A or B above)
./install.sh

# 3. set up the host
fpod init                 # or: fpod init --port 8080 to skip the sysctl
fpod doctor               # confirm the stack is healthy

# 4. create benches as usual
fpod create demo
```

Notes:
- **Private repo**: cloning needs credentials — easiest is `gh auth login` then
  `gh repo clone QuarkCyberSystems/fpod`, or add an SSH key and use the `git@` URL.
- **First `fpod init` on each machine** pulls the service images (~3 GB) and
  starts traefik/mariadb/redis/mailpit/adminer. One-time per host.
- **Benches don't transfer between machines** by copying `~/.fpod`. To move a
  site, `fpod backup` on the source and restore the dump on the target (restore
  command is post-MVP; for now use `bench restore` inside the target container).

---

## Day-to-day

```bash
# Create + use
fpod create demo
fpod shell demo                          # interactive shell in the bench container
fpod code demo                           # launch VS Code attached
fpod logs demo -f                        # follow honcho output
fpod logs demo --service web -f          # only web.1 lines

# Apps
fpod install-app demo erpnext --branch version-15
fpod migrate demo
fpod backup demo                         # writes to ~/.fpod/backups/demo/<timestamp>/

# Frappe v16 — the 3.14 interpreter is selected automatically
fpod create erp16 --branch version-16
fpod install-app erp16 erpnext           # inherits the bench's version-16

# Lifecycle
fpod stop demo                           # services stay up
fpod start demo
fpod restart demo
fpod list                                # table of all benches
fpod delete demo --keep-data             # archives to ~/.fpod/archive/

# Health
fpod doctor                              # PASS/WARN/FAIL table
fpod services status
```

---

## Caveats worth knowing

- **Default branch is `version-15`; both v15 and v16 work.** The bench image is
  `debian:bookworm-slim` and installs **no system Python** — every interpreter is
  a pyenv shim (3.12 and 3.14; `pyenv global 3.14 3.12`). fpod picks the right one
  from the branch: v15 → `python3.12`, v16/develop → `python3.14`.
  ```bash
  fpod create erp16 --branch version-16   # no --python needed
  fpod install-app erp16 erpnext          # inherits version-16 from the bench
  ```
  `--python` still overrides, but a combination that cannot work is rejected up
  front rather than failing minutes into `bench init`:
  ```
  $ fpod create erp16 --branch version-16 --python /home/frappe/.pyenv/shims/python3.12
  error: branch 'version-16' needs Python >= 3.14, but --python … is 3.12.
  ```
  The default stays v15 because `:latest` is a moving tag.
- **Dev mode only.** No `bench start` → gunicorn switchover. Use `frappe_docker`
  directly if you need a prod-mode deployment.
- **`*.localhost` resolution** comes for free on any modern Linux resolver
  (RFC 6761). No `/etc/hosts` edits needed.
- **SteamOS DNS quirk**: the host's `/etc/resolv.conf` points at systemd-resolved
  (`127.0.0.53`), which containers can't reach. The bench compose passes
  `dns: [1.1.1.1, 8.8.8.8]` so aardvark-dns has an upstream to forward to.
- **`install-app` of heavy apps** (like ERPNext) sometimes gets SIGKILL'd
  mid-install. Cause is unclear (host had ample RAM). `install-app` is now
  idempotent — just rerun the same command.

---

## Troubleshooting

```bash
fpod doctor                              # first stop for anything broken
fpod logs <name> -f                      # bench container logs
fpod services logs traefik -f            # routing issues
podman exec frappe-<name> bash           # poke around manually
```

If `fpod doctor` reports a FAIL, fix that first — most other commands assume
the basics are working.

---

## Credits

- **[Frappe-Manager](https://github.com/rtCamp/Frappe-Manager)** by
  [rtCamp](https://rtcamp.com/) — the Docker-based original that inspired fpod's
  command surface and shared-services architecture. If you're on Docker, use it.
- **[Frappe](https://frappeframework.com/)** & **[ERPNext](https://erpnext.com/)**
  by Frappe Technologies — the framework and ERP this all exists to run.
- **[Podman](https://podman.io/)**, **[Traefik](https://traefik.io/)**,
  **[MariaDB](https://mariadb.org/)**, **[Redis](https://redis.io/)**,
  **[Mailpit](https://mailpit.axllent.org/)**, and
  **[Adminer](https://www.adminer.org/)** — the stack underneath.

---

## License

MIT.

---

## Uninstall

```bash
fpod services stop                       # tear down shared services
podman network rm fpod-net               # remove the bridge
rm -rf ~/.fpod                           # all benches + config + backups
rm -rf ~/fpod                            # source
podman system prune -a                   # drop pulled images
```
