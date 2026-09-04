# TASK: Build a Fully Offline `.deb` Package for This Python Project

## Objective

Produce a single Debian package (`.deb`) that, when installed on a target machine
with **no internet access**, will:

1. Install the Python project and **all** of its dependencies (Python + system level).
2. Require **zero downloads at install time** — everything must be pre-downloaded
   and bundled inside the `.deb` itself.
3. Register and start the project's service(s) automatically after install, and
   have them survive a reboot.
4. Cleanly stop and remove everything on `apt remove` / `apt purge`.

---

## WORKING MODE — READ THIS FIRST

You must work in **three strictly ordered phases**. Do not skip ahead.

```
PHASE 1: ANALYSE   ->   PHASE 2: PLAN   ->   [WAIT FOR MY APPROVAL]   ->   PHASE 3: EXECUTE
```

- **Do not write any packaging code, scripts, or files during Phase 1 or Phase 2.**
- At the end of Phase 2 you must **stop and ask for my explicit approval**.
- If I ask for changes to the plan, revise the plan and ask again.
- Only after I reply with approval do you begin Phase 3.
- If during Phase 3 you discover the plan was wrong, **stop**, explain the
  discrepancy, propose a plan amendment, and wait for approval again.

---

## PHASE 1 — ANALYSE THE CODEBASE

Read the actual code. Do not guess, and do not assume a standard layout.
Record findings with the file path and line/section that supports each one.

### 1.1 Project structure & entry points
- Identify the repository layout, top-level packages/modules, and the project root.
- Locate every runnable entry point: `__main__.py`, `if __name__ == "__main__"`,
  CLI definitions (`argparse`, `click`, `typer`), ASGI/WSGI app objects
  (FastAPI/Flask/Django), Celery apps, schedulers, workers, consumers, daemons.
- Determine which of these must run as long-lived services vs. which are
  one-shot commands or dev-only utilities.
- Note existing packaging metadata: `pyproject.toml`, `setup.py`, `setup.cfg`,
  `MANIFEST.in`, `requirements*.txt`, `Pipfile`, `poetry.lock`, `uv.lock`,
  `environment.yml`, `Dockerfile`, `docker-compose.yml`, existing `debian/` dir.

### 1.2 Python dependency graph
- Resolve the **complete transitive** dependency set, not just direct requirements.
- Report the exact pinned version for each. If the project is unpinned, say so
  and propose a lock strategy.
- Cross-check declared dependencies against actual `import` statements in the
  source. Flag both directions:
  - imported but not declared (will break at runtime), and
  - declared but never imported (bloat / possibly stale).
- Identify the required Python version(s) and whether the target Debian/Ubuntu
  release ships a compatible interpreter.
- Flag packages that:
  - are **native/compiled extensions** (need matching manylinux wheels or a
    build toolchain — e.g. `numpy`, `pandas`, `scipy`, `lxml`, `cryptography`,
    `psycopg2`, `pillow`, `grpcio`, `pyzmq`, `torch`);
  - are **sdist-only** (no wheel available — needs build deps at package time);
  - are platform/architecture specific;
  - pull in **very large** artifacts (note the size).

### 1.3 Non-Python and system dependencies
- Shared libraries needed at runtime (e.g. `libpq5`, `libjpeg`, `libssl`,
  `libffi`, `libxml2`, `ffmpeg`, `tesseract`, `chromium`/webdriver).
- External services the project talks to (PostgreSQL, MySQL, Redis, RabbitMQ,
  MongoDB, Kafka, etc.). For each, decide and state clearly whether it is:
  - bundled/declared as a package dependency, or
  - assumed to be pre-existing and supplied via configuration.
- Any binary tools invoked via `subprocess` / `os.system`.

### 1.4 Runtime requirements
- All environment variables and config files read by the code, with defaults
  and which ones are **mandatory**.
- Ports bound and hostnames/interfaces used.
- Filesystem paths written to: data dirs, logs, caches, sockets, PID files,
  uploads, temp dirs. Note anything hardcoded to a relative path or `$HOME` —
  these will break under a system service and must be fixed or overridden.
- Database migrations or first-run initialisation steps.
- Static assets, templates, ML models, certificates, or data files that must
  ship with the package.
- Required OS user/group, and the minimum privileges actually needed.
- Startup ordering and dependencies between the project's own services.

### 1.5 Target environment
- State the assumed target: distro + release + architecture
  (e.g. Ubuntu 22.04 / Debian 12, `amd64` / `arm64`).
- **If this is not specified anywhere in the repo, ask me before proceeding.**
- Confirm the build environment matches the target (native extensions and
  bundled `.deb` dependencies are release-specific).

### 1.6 Phase 1 deliverable
A written analysis report covering all of the above, plus:
- An explicit list of **open questions / assumptions** you need me to confirm.
- An explicit list of **risks and blockers** (e.g. "`torch` adds 2.5 GB to the
  package", "`X` is sdist-only and needs a compiler on the build host").
- A **Mermaid** diagram of the dependency and service topology.

Use Markdown. Use ASCII art or Mermaid for any diagram — **do not produce
HTML or CSS**.

---

## PHASE 2 — PROPOSE A PLAN

Based only on Phase 1 findings, propose a concrete plan. It must decide and
justify each of the following.

### 2.1 Packaging approach
Evaluate the realistic options and recommend one, with reasoning:

| Approach | Notes to evaluate |
|---|---|
| Bundled venv under `/opt/<project>` with vendored wheels | Usually the best fit for "fully offline". |
| `dh-virtualenv` | Mature, but build-host coupling. |
| Vendored wheelhouse + `pip install --no-index --find-links` in `postinst` | Simple; still offline. |
| Native Debian packaging (`python3-*` deps from apt) | Cleanest, but only if every dep exists in the target's apt repos. |
| PEX / zipapp / PyInstaller single artifact | Fewer moving parts; harder to patch. |

Explicitly state how the chosen approach guarantees **no network access at
install time**.

### 2.2 Layout and paths
Propose the on-disk layout, e.g.:

```
/opt/<project>/                  application code
/opt/<project>/venv/             bundled virtualenv (or vendored interpreter)
/opt/<project>/wheels/           pre-downloaded wheels (build-time only, if not stripped)
/etc/<project>/config.yaml       configuration (marked as a dpkg conffile)
/etc/default/<project>           environment overrides
/var/lib/<project>/              persistent state
/var/log/<project>/              logs
/usr/lib/systemd/system/*.service   unit files
/usr/bin/<project>               wrapper entry point
```

### 2.3 Dependency bundling strategy
- How wheels will be collected (`pip download` / `pip wheel` with the correct
  `--platform`, `--python-version`, `--implementation`, `--abi`, `--only-binary`).
- How sdist-only packages will be built into wheels ahead of time.
- Whether the venv is built at **package build time** (preferred, reproducible)
  or in `postinst` from the vendored wheelhouse.
- How system `.deb` dependencies are handled: declared via `Depends:` (requires
  apt cache on target) **or** vendored as bundled `.deb` files installed by
  `postinst`. State the trade-off and pick one.
- Estimated final package size.

### 2.4 Service management
- One systemd unit per long-running process, named and listed.
- For each unit: `ExecStart`, `WorkingDirectory`, `User`/`Group`,
  `EnvironmentFile`, `Restart=`, `After=`/`Requires=`, and hardening
  (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, `ReadWritePaths`).
- Use a `.target` to group units if there are several.
- How units get enabled and started on install (`dh_installsystemd` /
  `deb-systemd-invoke`), restarted on upgrade, and stopped on removal.

### 2.5 Maintainer scripts
Specify exactly what each script does, and confirm each is **idempotent** and
**offline**:
- `preinst` — pre-flight checks.
- `postinst` — create user/group, create dirs + permissions, materialise the
  venv if needed, generate config, run migrations, enable + start services.
- `prerm` — stop and disable services.
- `postrm` — clean up on `remove`; on `purge` also remove config, data, user.
- Correct handling of the `configure`/`upgrade`/`abort-*` argument cases.

### 2.6 Control metadata
`Package`, `Version`, `Architecture`, `Maintainer`, `Depends`, `Pre-Depends`,
`Recommends`, `Conflicts`, `Replaces`, `Description`, `Section`, `Priority`,
plus `conffiles` and `postinst`/`prerm` hooks.

### 2.7 Build pipeline
- The exact build toolchain (`dpkg-deb` + hand-rolled tree, or
  `debhelper`/`dpkg-buildpackage`, or `fpm`) and why.
- A single reproducible build command or `Makefile`/script target.
- Whether the build must run inside a container matching the target release.

### 2.8 Verification plan
How you will prove it works:
- `lintian` clean (or documented exceptions).
- Install in a **network-disabled** clean container of the target release.
- Assert: files placed correctly, services `active (running)`, health/port check
  passes, logs clean.
- Reboot survival, `apt upgrade` to a new version, `apt remove`, `apt purge`.

### 2.9 Phase 2 deliverable
- The plan, as Markdown.
- A **Mermaid** diagram of the build pipeline and the install-time flow.
- A file-by-file list of everything you intend to create or modify.
- A step-by-step execution order for Phase 3.

**Then STOP and ask me to approve the plan. Do not start Phase 3 until I do.**

---

## PHASE 3 — EXECUTE (only after approval)

Work through the approved execution order. After each step, report what you did
and what you verified.

1. Create the packaging directory structure and all metadata/maintainer scripts.
2. Vendor the wheelhouse and any bundled system `.deb` files.
3. Write the systemd units and config templates.
4. Write the build script.
5. Build the `.deb`.
6. Run the full verification plan from 2.8 in an offline container.
7. Deliver:
   - the built `.deb` (with its size and SHA256),
   - `BUILD.md` — how to rebuild from scratch,
   - `INSTALL.md` — how to install, configure, verify, and uninstall,
   - a short summary of anything deferred or known-limited.

---

## HARD CONSTRAINTS

- **No network access at install time.** If any step would download something
  during `apt install`, the design is wrong — fix it.
- Never invent dependencies, versions, paths, or entry points. Everything must
  be traceable to something you actually read in the repo. If you cannot
  determine something, **ask**.
- Never place secrets in the package. Config with secrets must be generated or
  supplied at install time, with `0640` permissions or tighter.
- Services must not run as `root` unless you can justify it explicitly.
- Config files must be proper dpkg `conffiles` so user edits survive upgrades.
- `apt purge` must leave no orphaned units, users, or directories.
- Maintainer scripts must be idempotent and must `set -e`.
- Documentation output: Markdown only. Diagrams as **Mermaid or ASCII art**.
  **Do not generate HTML or CSS.**

---

## START HERE

Begin **Phase 1** now. Do not write any packaging files yet. Produce the
analysis report and your list of open questions, then move to Phase 2.
