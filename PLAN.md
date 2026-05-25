# fpod — Frappe-on-Podman CLI

A Python CLI that manages multiple isolated Frappe benches on rootless Podman,
with shared backing services and Traefik-based hostname routing.

Working name: **fpod** (open to change; see §11).

---

## 1. Goals & non-goals

### Goals (in scope)
- One CLI to create, start, stop, attach, and delete Frappe benches.
- Shared MariaDB + Redis + Traefik across all benches (matches `fm` architecture).
- Per-bench Frappe container with `bench start` (dev mode, honcho).
- Mailpit + Adminer as built-in shared services.
- Hostname routing: `<bench>.localhost`, `mail.localhost`, `db.localhost`.
- Convenience wrappers for common `bench` commands (install-app, migrate, backup).
- VS Code attach helper (`fpod code <bench>`).
- Pipx/uv-installable Python package.

### Non-goals (explicitly out of scope)
- **Production mode** (gunicorn/supervisor/real nginx). Dev-mode only.
- **SSL / Let's Encrypt.** Localhost-only by design.
- **Remote deployment / cloud provisioning.**
- **Cross-platform support.** Linux + rootless Podman only. macOS, Windows, WSL not targeted.
- **GUI.** CLI only.
- **Migration tool for existing `fm` benches.** Fresh-start tool.

### Explicit incompatibility with current `~/frappe-stack/`
The bench we built earlier lives at `~/frappe-stack/frappe_docker/.devcontainer/`.
`fpod` will not adopt it. We leave it alone; `fpod` builds fresh benches under
`~/.fpod/`. The two can coexist (different ports, different networks).

---

## 2. Architecture

```
                                  Host (Steam Deck, rootless podman)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │                                                                            │
 │   ┌────────────────── fpod-net (user-defined podman bridge) ──────────┐    │
 │   │                                                                   │    │
 │   │  ┌─────────── shared services (always on) ──────────┐             │    │
 │   │  │  traefik   mariadb  redis-cache  redis-queue     │             │    │
 │   │  │              ▲       redis-socketio              │             │    │
 │   │  │              │       mailpit  adminer            │             │    │
 │   │  │              │                                   │             │    │
 │   │  └──────────────┼───────────────────────────────────┘             │    │
 │   │                 │                                                 │    │
 │   │  ┌──────────────┴──── per-bench frappe containers ────┐          │    │
 │   │  │  frappe-demo      frappe-sandbox     frappe-erpnext│          │    │
 │   │  │   (bench start)    (bench start)      (bench start)│          │    │
 │   │  └────────────────────────────────────────────────────┘          │    │
 │   │                                                                   │    │
 │   └───────────────────────────────────────────────────────────────────┘    │
 │              ▲                                                              │
 │              │  :80 (or :8080) ──── host port, owned by traefik              │
 │              │                                                              │
 │     http://demo.localhost ────► traefik ────► frappe-demo:8000 (web)        │
 │     http://demo.localhost/socket.io/* ──────► frappe-demo:9000 (socketio)   │
 │     http://mail.localhost ──────────────────► mailpit:8025                  │
 │     http://db.localhost   ──────────────────► adminer:8080                  │
 │                                                                              │
 └────────────────────────────────────────────────────────────────────────────┘
```

### Why one shared MariaDB
Frappe's default model is **one DB per site** inside a single MariaDB server.
Each site gets a random DB name + user (already what `bench new-site`
generates). No reason to run N MariaDB containers.

### Why three Redis containers (not one)
Frappe expects `redis_cache`, `redis_queue`, `redis_socketio` as distinct
endpoints. Even though they *could* share an instance, every Frappe doc and
deployment template uses three. We match the convention so people copying
config between fpod and `frappe_docker` aren't surprised.

### Why a custom bridge network (`fpod-net`)
- Stable DNS names between containers (`mariadb`, `redis-cache`, etc.).
- Lets us add/remove benches without recreating the whole stack.
- Traefik's docker provider auto-discovers anything on this network with the
  right labels.

### Why Traefik instead of nginx
- Auto-discovery via labels (zero config to wire a new bench in).
- Speaks the docker/podman socket API natively.
- Hot-reloads on container add/remove.

`fm` uses nginx with templated configs. Works fine, but requires a reload
step on every bench add. Traefik is the simpler match for our model.

---

## 3. On-disk layout

```
~/.fpod/
├── config.toml                       # global config (port, base domain, etc.)
├── services/
│   ├── compose.yaml                  # generated, do not hand-edit
│   ├── traefik/
│   │   └── dynamic.yaml              # any static traefik rules
│   └── data/                         # named volumes mounted here for backup-ability
│       ├── mariadb/
│       └── mailpit/
└── benches/
    ├── demo/
    │   ├── bench.toml                # per-bench manifest
    │   ├── compose.yaml              # generated, do not hand-edit
    │   ├── entrypoint.sh             # generated; runs in container
    │   └── frappe-bench/             # the actual bench (bind-mounted to /workspace)
    │       ├── apps/
    │       ├── sites/
    │       ├── config/
    │       ├── env/
    │       └── logs/
    └── sandbox/
        └── ...

~/.local/bin/fpod                     # pipx-managed wrapper
~/.local/pipx/venvs/fpod/             # actual install
```

### `config.toml` example

```toml
[fpod]
version = "0.1.0"
base_domain = "localhost"           # bench at <name>.<base_domain>
host_port = 80                      # traefik bound here
data_dir = "/home/deck/.fpod"

[podman]
network = "fpod-net"
socket = "/run/user/1000/podman/podman.sock"
compose_bin = "/home/deck/frappe-stack/.venv/bin/podman-compose"  # reused

[services]
mariadb_image = "docker.io/mariadb:11.8"
redis_image   = "docker.io/redis:alpine"
traefik_image = "docker.io/traefik:v3.2"
mailpit_image = "docker.io/axllent/mailpit:latest"
adminer_image = "docker.io/adminer:latest"
mariadb_root_password = "<random 32-char, generated at init>"

[bench_defaults]
frappe_image  = "docker.io/frappe/bench:latest"
# Reality check (2026-05): Frappe `version-16` HEAD now requires Python 3.14,
# but `docker.io/frappe/bench:latest` ships only 3.11 + 3.12. Until a 3.14
# bench image lands upstream, default to v15. v16 is reachable later via a
# custom image with Python 3.14 (see post-MVP §12).
frappe_branch = "version-15"
python        = "/usr/bin/python3.11"
developer_mode = true
admin_password = "admin"           # default for new sites; user can override
```

### `bench.toml` example (per-bench)

```toml
name = "demo"
site = "demo.localhost"
created = "2026-05-23T09:55:00+04:00"
frappe_branch = "version-15"
apps = ["frappe"]                  # extended as install-app is called
db_name = "_7ad329d6da0dcdc2"      # captured from new-site output for backup/restore
state = "running"                  # running | stopped | broken
```

---

## 4. Command surface

All commands return non-zero on failure with a clear error. All long-running
operations stream progress.

```
fpod init                              one-time host setup
fpod create <name> [--branch X] [--apps a,b,c] [--admin-password P]
fpod start  <name>
fpod stop   <name>
fpod restart <name>
fpod delete <name> [--keep-data]
fpod list                              table of benches: name, state, URL, apps
fpod shell  <name>                     drops into bash in the frappe container
fpod code   <name>                     prints vscode-remote URI; --launch to open
fpod logs   <name> [--service web|worker|socketio|...]  [-f]
fpod install-app <name> <app> [--branch X]
fpod migrate <name>
fpod backup  <name> [--out PATH]
fpod services start|stop|restart|status|logs
fpod doctor                            checks: podman, sockets, ports, network
fpod version
```

### Per-command behavior

#### `fpod init`
- Validates: `podman --version`, ability to bind chosen port, `python3 -c 'import tomllib'`.
- Creates `~/.fpod/` tree.
- Generates `config.toml` with a random `mariadb_root_password`.
- `systemctl --user enable --now podman.socket` (needed for traefik's docker provider).
- Creates `fpod-net` podman network.
- Renders `services/compose.yaml` from template.
- Pulls service images.
- Brings shared services pod up.
- Idempotent (rerun = no-op if everything already exists; refreshes images if
  `--update` is passed).
- **Failure modes**: port already bound → error with suggestion. Podman not
  installed → error with install hint. `~/.fpod/` exists with different
  version → migration prompt (post-MVP).

#### `fpod create <name>`
The most complex command. Steps:
1. Validate `name`:
   - Regex `^[a-z][a-z0-9-]{1,30}$`
   - Not in reserved list: `services`, `traefik`, `mail`, `db`, `mariadb`, `redis*`
   - Doesn't already exist in `~/.fpod/benches/`
2. Ensure services are up (auto-start if not).
3. `mkdir ~/.fpod/benches/<name>/`
4. Render `bench.compose.yaml` from template, with traefik labels:
   ```
   traefik.enable=true
   traefik.docker.network=fpod-net
   traefik.http.routers.<name>-web.rule=Host(`<name>.localhost`)
   traefik.http.routers.<name>-web.service=<name>-web
   traefik.http.services.<name>-web.loadbalancer.server.port=8000
   traefik.http.routers.<name>-sio.rule=Host(`<name>.localhost`) && PathPrefix(`/socket.io`)
   traefik.http.routers.<name>-sio.service=<name>-sio
   traefik.http.services.<name>-sio.loadbalancer.server.port=9000
   ```
5. Render `entrypoint.sh`:
   ```bash
   #!/bin/bash
   set -e
   cd /workspace
   if [ ! -f .fpod-initialized ]; then
     bench init --skip-redis-config-generation \
       --frappe-branch ${BENCH_BRANCH} \
       --python ${BENCH_PYTHON} \
       frappe-bench
     cd frappe-bench
     bench set-config -g db_host mariadb
     bench set-config -gp db_port 3306
     bench set-config -g redis_cache redis://redis-cache:6379
     bench set-config -g redis_queue redis://redis-queue:6379
     bench set-config -g redis_socketio redis://redis-socketio:6379
     sed -i '/redis/d' Procfile
     bench new-site \
       --mariadb-root-password "${MARIADB_ROOT_PASS}" \
       --admin-password "${ADMIN_PASS}" \
       --mariadb-user-host-login-scope=% \
       "${SITE_NAME}"
     [ "${DEV_MODE}" = "1" ] && bench --site "${SITE_NAME}" set-config developer_mode 1
     bench use "${SITE_NAME}"
     cd /workspace
     touch .fpod-initialized
   fi
   cd /workspace/frappe-bench
   exec bench start
   ```
6. `podman-compose -p fpod-<name> --in-pod=false up -d`
7. `podman logs -f` until `.fpod-initialized` exists or container exits.
8. Write `bench.toml` manifest.
9. Print site URL.

**Failure recovery**: if init fails mid-flight, the bench is marked
`state = "broken"` in the manifest. `fpod delete <name> --force` cleans it up.
`fpod create <name> --resume` re-runs the entrypoint (which is idempotent due
to the flag file).

**Time budget**: 10–15 min on first run per bench (bench init dominates).

#### `fpod start <name>`
- Reads manifest.
- Ensures services are up.
- `podman start frappe-<name>` (or `podman-compose -p fpod-<name> up -d` if
  the container doesn't exist).
- Polls `http://<name>.localhost` until 200.

#### `fpod stop <name>`
- `podman stop frappe-<name>` (gracefully SIGTERMs honcho).
- Services pod stays up. Use `fpod services stop` to bring services down.

#### `fpod delete <name>`
- Stop container.
- `podman rm` container.
- `bench drop-site` against MariaDB to free the database (skip with `--keep-data`).
- `rm -rf ~/.fpod/benches/<name>/` (with `--keep-data`, moves to `~/.fpod/archive/<name>-<timestamp>/`).
- Remove manifest.
- Confirm prompt unless `--yes`.

#### `fpod shell <name>`
- `podman exec -it frappe-<name> bash -lc 'cd /workspace/frappe-bench && exec bash'`.

#### `fpod code <name>`
- Computes the vscode-remote URI:
  `vscode-remote://attached-container+<hex(container_name)>/workspace/frappe-bench`
- **Default: launches VS Code** via `code --folder-uri <uri>`.
- `--print` flag prints the URI instead of launching (useful for piping, or
  when `code` isn't on PATH).
- If `code` not on PATH, falls back to printing with a one-line note.

#### `fpod logs <name>`
- Default: `podman logs -f frappe-<name>` (entire honcho output).
- `--service web|worker|socketio|schedule|watch`: tails the per-process log
  inside the container at `frappe-bench/logs/<service>.log`.

#### `fpod install-app <name> <app>`
- `podman exec frappe-<name> bash -lc "cd /workspace/frappe-bench && bench get-app --branch ${BRANCH} ${APP}"`
- `bench --site <site> install-app <app>`
- Updates `bench.toml` apps list.

#### `fpod migrate <name>`
- `podman exec frappe-<name> bash -lc "cd /workspace/frappe-bench && bench --site <site> migrate"`

#### `fpod backup <name>`
- `bench --site <site> backup --with-files`
- Copies the dump out of the container to `~/.fpod/backups/<name>/<timestamp>/`.

#### `fpod doctor`
Health-check command, prints a table:
```
PASS  Podman installed (5.3.2)
PASS  Podman socket reachable
PASS  fpod-net network exists
PASS  Services pod up
PASS  Port 80 listening (traefik)
WARN  Linger off → benches stop on logout (fix: sudo loginctl enable-linger deck)
PASS  MariaDB accepting connections
PASS  Redis cache reachable
```

---

## 5. Networking & port strategy

### Host port for traefik

**Decision: default to `80`.** Requires:
```bash
sudo sysctl net.ipv4.ip_unprivileged_port_start=80
# or persistently:
echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/99-fpod.conf
```

`fpod init` checks this and prints the exact command if it's not set. We
don't run sudo ourselves.

**Fallback**: `--port 8080` (or any unprivileged port). URLs become
`http://<bench>.localhost:8080`. Slightly uglier, no sysctl required.

### .localhost resolution
- All modern Linux resolvers route `*.localhost → 127.0.0.1` per RFC 6761.
- No `/etc/hosts` edits needed.
- Confirmed working on SteamOS systemd-resolved.

### Inter-container DNS
Podman's built-in DNS (aardvark-dns) gives every container a name on
`fpod-net`. So `mariadb`, `redis-cache`, etc., just resolve.

### Userns mapping
Per-bench frappe containers get `userns_mode: "keep-id:uid=1000,gid=1000"` so
the bind-mounted bench dir is owned by host UID 1000 (= `deck`). Services
containers use default mapping (named volumes, no host UID concern).

---

## 6. Container lifecycle decisions

### Pod or no pod?
**No pods.** Rationale:
- `userns_mode: keep-id` is incompatible with `--pod` (the bug we hit earlier).
- Pods give shared netns, but our containers communicate over a bridge
  network anyway. We don't need shared netns.

### Compose or direct podman calls?
**Compose (via `podman-compose`).** Rationale:
- Declarative compose files are easier to read, version, and debug.
- We already have `podman-compose` installed and working.
- `--in-pod=false` works around the userns issue.
- Direct `podman run` calls give finer control but multiply the code we have to write.

We reuse `~/frappe-stack/.venv/bin/podman-compose` for now; `fpod init` can
later install its own pinned copy into the fpod venv.

### Container restart policy
All containers: `restart: unless-stopped`. Combined with podman's
`generate systemd` (or user manually running `systemctl --user enable
fpod-services.service`), benches survive reboots if linger is on.

We do **not** ship systemd integration in MVP. Users can layer it on top.

---

## 7. Package structure

```
fpod/
├── pyproject.toml
├── README.md
├── PLAN.md                          # this document
├── src/fpod/
│   ├── __init__.py
│   ├── __main__.py                  # python -m fpod
│   ├── cli.py                       # Typer app + command registration
│   ├── config.py                    # load_config(), save_config()
│   ├── manifest.py                  # load_manifest(name), save_manifest()
│   ├── compose.py                   # render templates, parse compose output
│   ├── podman.py                    # subprocess wrappers
│   ├── services.py                  # shared services lifecycle
│   ├── bench.py                     # per-bench lifecycle
│   ├── templates/
│   │   ├── services.compose.yaml.j2
│   │   ├── bench.compose.yaml.j2
│   │   ├── entrypoint.sh.j2
│   │   └── traefik.dynamic.yaml.j2
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── init.py
│   │   ├── create.py
│   │   ├── start.py
│   │   ├── stop.py
│   │   ├── restart.py
│   │   ├── delete.py
│   │   ├── list_.py
│   │   ├── shell.py
│   │   ├── code.py
│   │   ├── logs.py
│   │   ├── install_app.py
│   │   ├── migrate.py
│   │   ├── backup.py
│   │   ├── services.py
│   │   └── doctor.py
│   └── errors.py                    # custom exception types
└── tests/
    ├── test_manifest.py
    ├── test_compose_rendering.py
    └── test_smoke.sh                # end-to-end shell test
```

### Dependencies

```toml
[project]
name = "fpod"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer >= 0.12",        # CLI
    "jinja2 >= 3.1",        # templates
    "rich >= 13",           # tables, progress (typer pulls it as extra anyway)
    "tomli-w >= 1.0",       # write toml (read is stdlib >= 3.11)
    "httpx >= 0.27",        # health checks (curl wrapper)
]

[project.scripts]
fpod = "fpod.cli:app"
```

`podman-compose` is a system dep, not a pip dep — we shell out to the binary,
we don't import its Python.

---

## 8. Build & install path

### Development (the way we'll build it)
```bash
cd ~/fpod
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/fpod --help
```

### User install (post-MVP)
```bash
pipx install fpod          # if we publish to PyPI
# or
pipx install git+https://github.com/<you>/fpod.git
```

For SteamOS specifically: pipx needs to exist first. We'll document
`python3 -m pip install --user pipx --break-system-packages` (or the venv
workaround) in the README.

---

## 9. Build phases (the actual order we'll write code in)

| Phase | Deliverable | Validation |
|---|---|---|
| **P1. Skeleton** | `pyproject.toml`, `cli.py` with all command stubs (each just `print("TODO")`), config load/save. | `pip install -e .`, `fpod --help` shows all commands. |
| **P2. Services** | `fpod init`, `fpod services *`, services compose template. Traefik dashboard reachable at `traefik.localhost`. | `curl mariadb` from inside another container resolves. |
| **P3. Bench create** | `fpod create <name>` + entrypoint script + bench compose template. | `fpod create demo && curl http://demo.localhost` returns 200. |
| **P4. Lifecycle** | `start`, `stop`, `restart`, `delete`, `list`, `shell`, `code`, `logs`. | Full create → use → stop → delete loop works. |
| **P5. Bench wrappers** | `install-app`, `migrate`, `backup`. | `fpod install-app demo erpnext` succeeds. |
| **P6. Polish** | `doctor`, error messages, README, smoke test script. | `bash tests/test_smoke.sh` passes clean. |

Each phase ends with a working system at that level. We don't move on until
the previous phase's validation passes.

**Time estimate**: ~3 focused days. P3 is the biggest single chunk (~1 day).

---

## 10. What we'll do about the existing `~/frappe-stack/`

**Leave it alone.** It runs on its own network, its own compose project name
(`frappe-dev`), and its own port (`127.0.0.1:8000`). It will not collide
with fpod's `fpod-net` + `:80` traefik.

If port 80 conflicts ever happen (it won't unless the user manually maps
fpod to 8000), we'll handle it then.

We will **not** write an importer. The existing bench was for getting our
hands dirty; fpod creates fresh benches. The user can `fpod create dev`
later if they want fpod to own a "dev" bench.

---

## 11. Decisions log

Resolved 2026-05-23:

| # | Question | Decision |
|---|---|---|
| 1 | Tool name | **`fpod`** |
| 2 | Default host port | **`80`** with one-time `sysctl net.ipv4.ip_unprivileged_port_start=80`; `--port` flag overrides; `fpod doctor` checks. |
| 3 | Bench branch default | **`version-15`** for now. v16 was the intended default, but as of 2026-05 v16 HEAD requires Python 3.14 while `frappe/bench:latest` only ships 3.11/3.12. Revisit when upstream image catches up. |
| 4 | `fpod code` behavior | **Auto-launch** VS Code via `code --folder-uri ...`. `--print` flag opts out. Graceful fallback to print if `code` not on PATH. |
| 5 | Traefik dashboard | **Enabled at `http://traefik.localhost`**, read-only, loopback-only. Useful for debugging routing in dev. |
| 6 | Filesystem layout | Source: `~/fpod/`. Runtime data: `~/.fpod/`. |
| 7 | Tests | Smoke shell script (`tests/test_smoke.sh`) end-to-end + minimal pytest for Jinja template rendering. No subprocess mocking. |

---

## 12. Out-of-scope items we'll add later (post-MVP)

Captured here so they're not forgotten, but not built in v0.1:

- `fpod import <path>` — adopt an existing `frappe-bench` directory.
- `fpod export <name>` — produce a portable archive (db dump + files + apps list).
- `fpod update` — pull new frappe/bench images and recreate.
- `fpod logs --since 1h` time filtering.
- Multi-architecture image awareness (ARM Decks don't exist, but).
- `fpod ssl` — Let's Encrypt via traefik (only if anyone ever asks).
- Production-mode benches (gunicorn + nginx). Deliberately deferred indefinitely.
- Web dashboard.
- Auto-restart on bench config change (file watcher for compose template).

---

## 13. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Frappe upstream changes `bench init` flags between versions | Medium | Pin the bench image, document upgrade path. |
| Podman 5.x compose semantics drift | Low | We've tested on 5.3.2; CI smoke test catches regressions. |
| Traefik 3.x label syntax changes | Low | Pin image to `traefik:v3.2`. |
| `userns_mode: keep-id` quirks on future kernel/podman | Low | Doctor command flags it; we have the `--in-pod=false` workaround documented. |
| Port 80 binding fails on SteamOS updates resetting sysctl | Medium | `fpod doctor` detects, `fpod init --port 8080` fallback. |
| `bench start` (honcho) dies silently inside container | Medium | Healthcheck on `/api/method/ping`; container restart policy. |
| User's `~/.fpod/` grows large (each bench ~1GB) | Certain | `fpod list` shows sizes; `fpod delete --keep-data` archives. |

---

## 14. Success criteria for v0.1

The MVP is "done" when:

1. `fpod init` succeeds on a clean SteamOS install (rootless podman only).
2. `fpod create demo` produces a working site at `http://demo.localhost`
   within 15 minutes on Deck hardware.
3. `fpod create sandbox` while `demo` is running creates a second
   independent bench, also accessible at `http://sandbox.localhost`, no
   conflicts.
4. `fpod shell demo` and `fpod code demo` both work end-to-end.
5. `fpod install-app demo erpnext` installs ERPNext into demo and the
   ERPNext desk loads.
6. `fpod delete demo` removes everything, including DB, and `fpod list`
   shows only `sandbox`.
7. `fpod doctor` passes all checks (with `WARN` for linger if not enabled).
8. README walks a new user through install → first bench in under 20 minutes
   (excluding image pulls).

If any of those don't pass cleanly, v0.1 isn't done.
