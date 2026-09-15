"""notebooklm_setup.py — One-shot installer and login for NotebookLM integration.

This script bundles everything a user needs to start using
`process_notebook.py` into a single command that an agent (Claude Code,
Codex, etc.) can invoke on the user's behalf:

    1. Install `notebooklm-py[browser]` into the active Python (pip --user)
    2. Install the Playwright Chromium browser
    3. Locate the `notebooklm` CLI (or fall back to `python -m notebooklm`)
    4. Launch `notebooklm login` — this opens a Chromium window so the user
       can sign in to Google once. The storage_state.json is written to the
       default location after successful login.
    5. Verify the auth file now exists.

Usage:
    python3 scripts/notebooklm_setup.py               # full setup + login
    python3 scripts/notebooklm_setup.py --skip-login  # install only
    python3 scripts/notebooklm_setup.py --reinstall   # force reinstall pkg

Environments:
    * If run inside a virtualenv, packages are installed into that venv.
    * If run outside a venv, `pip install --user` is used. On systems with
      PEP 668 (Arch/Manjaro/Debian/etc.) that path is blocked — create a
      venv and re-run this script via `.venv/bin/python`.

Exit codes:
    0 — success (package installed and, unless --skip-login, auth is present)
    1 — installation or login failure
    2 — login completed but auth file still not found at expected paths
    3 — login requires an interactive TTY but stdin is not a terminal
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _auth_paths() -> list[Path]:
    """Candidate NotebookLM session files, upstream-aware.

    Prefers the path computed by `notebooklm.paths.get_storage_path()` (which
    respects the `NOTEBOOKLM_HOME` env var), then falls back to the legacy
    hard-coded defaults under `~/.notebooklm` and `~/.config/notebooklm`.
    """
    paths: list[Path] = []
    try:
        from notebooklm.paths import get_storage_path  # type: ignore[import-not-found]
        paths.append(Path(get_storage_path()))
    except Exception:
        pass

    home = Path.home()
    legacy = [
        home / ".notebooklm" / "storage_state.json",
        home / ".config" / "notebooklm" / "storage_state.json",
        home / ".notebooklm" / "default" / "storage_state.json",
    ]
    for p in legacy:
        if p not in paths:
            paths.append(p)
    return paths


AUTH_PATHS = _auth_paths()


# ── Logging ────────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(f">> {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


# ── Step runners ───────────────────────────────────────────────────────────────


def run_checked(cmd: list[str], desc: str) -> int:
    """Run a subprocess, stream stdout/stderr live, return exit code."""
    log(f"{desc}")
    log(f"   $ {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:
        err(f"Command not found while {desc.lower()}: {exc}")
        return 127
    if result.returncode != 0:
        err(f"Step failed ({desc}) with exit code {result.returncode}")
    return result.returncode


def in_venv() -> bool:
    """Return True when sys.executable points inside an active virtualenv."""
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def is_notebooklm_importable() -> bool:
    """Check whether `import notebooklm` works in the current interpreter."""
    probe = subprocess.run(
        [sys.executable, "-c", "import notebooklm"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def find_notebooklm_cli() -> list[str] | None:
    """Return an argv prefix that invokes the notebooklm CLI, or None."""
    resolved = shutil.which("notebooklm")
    if resolved:
        return [resolved]
    # Fall back to module invocation — works even when ~/.local/bin is not on PATH
    probe = subprocess.run(
        [sys.executable, "-m", "notebooklm", "--help"],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return [sys.executable, "-m", "notebooklm"]
    return None


_INLINE_AUTH_MARKER = Path("<NOTEBOOKLM_AUTH_JSON env var>")


def auth_file_exists() -> Path | None:
    """Return the first existing auth file, or a sentinel Path if the inline
    `NOTEBOOKLM_AUTH_JSON` env var is set, or None if neither is present.
    """
    if os.environ.get("NOTEBOOKLM_AUTH_JSON"):
        return _INLINE_AUTH_MARKER
    # Recompute on every call so a test or agent flipping NOTEBOOKLM_HOME at
    # runtime sees the new location immediately.
    for path in _auth_paths():
        if path.exists():
            return path
    return None


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot installer and login for the NotebookLM integration used "
            "by scripts/process_notebook.py. Safe to run repeatedly — "
            "re-installation is skipped if the package is already importable."
        )
    )
    parser.add_argument(
        "--skip-login",
        action="store_true",
        help="Install dependencies only; do not launch `notebooklm login`",
    )
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="Force reinstall notebooklm-py even if it is already importable",
    )
    args = parser.parse_args()

    # ── Step 1: install notebooklm-py[browser] ────────────────────────────────
    if args.reinstall or not is_notebooklm_importable():
        inside_venv = in_venv()
        pip_cmd = [sys.executable, "-m", "pip", "install"]
        if not inside_venv:
            pip_cmd.append("--user")
        pip_cmd.extend(
            [
                "--upgrade" if args.reinstall else "--upgrade-strategy=only-if-needed",
                "notebooklm-py[browser]",
            ]
        )
        scope = "venv" if inside_venv else "pip --user"
        log(f"Installing notebooklm-py[browser] ({scope})...")
        rc = run_checked(pip_cmd, desc="pip install notebooklm-py[browser]")
        if rc != 0:
            if not inside_venv:
                err(
                    "Failed to install notebooklm-py.\n\n"
                    "If pip refused with 'externally-managed-environment' (PEP 668 —\n"
                    "Arch/Manjaro/Debian/etc.), create a venv and re-run this script\n"
                    "using that venv's python:\n\n"
                    "  python3 -m venv .venv\n"
                    "  .venv/bin/python scripts/notebooklm_setup.py\n"
                )
            else:
                err(
                    "Failed to install notebooklm-py inside venv. Try manually:\n"
                    f"  {sys.executable} -m pip install 'notebooklm-py[browser]'"
                )
            sys.exit(1)
    else:
        log("notebooklm-py is already importable — skipping pip install")

    # ── Step 1b: install the repo's own dependencies ──────────────────────────
    # Step 1 installs the NotebookLM client and nothing else, which is enough to
    # log in and enough to run fetch_notebook.py — and not enough for anything
    # downstream of it. In a fresh venv `process_notebook.py` fetches the notes,
    # writes the staging JSON, then dies on `import yaml` in atomize.py, after
    # the slow part has already run. Installing requirements.txt into the SAME
    # interpreter closes that gap at setup time instead.
    requirements = Path(__file__).resolve().parent.parent / "requirements.txt"
    if requirements.is_file():
        pip_cmd = [sys.executable, "-m", "pip", "install"]
        if not in_venv():
            pip_cmd.append("--user")
        pip_cmd.extend(["-r", str(requirements)])
        log(f"Installing repo requirements from {requirements.name}...")
        rc = run_checked(pip_cmd, desc="pip install -r requirements.txt")
        if rc != 0:
            err(
                "Failed to install the repo requirements. The NotebookLM login will\n"
                "still work, but the processing pipeline will not. Try manually:\n"
                f"  {sys.executable} -m pip install -r {requirements}"
            )
            sys.exit(1)
    else:
        log(f"No requirements.txt at {requirements} — skipping")

    # ── Step 2: install Playwright Chromium ───────────────────────────────────
    # Safe to re-run; playwright will skip if the browser is already present.
    log("Ensuring Playwright Chromium is installed...")
    rc = run_checked(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        desc="playwright install chromium",
    )
    if rc != 0:
        err(
            "Playwright Chromium install failed. On Linux you may also need:\n"
            f"  {sys.executable} -m playwright install-deps chromium"
        )
        sys.exit(1)

    # ── Step 3: locate the notebooklm CLI ─────────────────────────────────────
    cli = find_notebooklm_cli()
    if cli is None:
        err(
            "notebooklm CLI not found even after install. Check that "
            "~/.local/bin is on PATH, or invoke directly with:\n"
            f"  {sys.executable} -m notebooklm login"
        )
        sys.exit(1)
    log(f"notebooklm CLI: {' '.join(cli)}")

    # ── Step 4: login (unless skipped) ────────────────────────────────────────
    if args.skip_login:
        log("Skipping login (--skip-login). Run `notebooklm login` later.")
        sys.exit(0)

    existing = auth_file_exists()
    if existing:
        log(f"Existing NotebookLM session found: {existing}")
        log("Re-running `notebooklm login` anyway to refresh cookies.")

    # `notebooklm login` opens a browser AND then blocks on input() until the
    # user presses ENTER in this shell. If stdin is not a TTY (e.g. invoked via
    # the Claude Code `!` prefix or a pipe), input() hits EOF immediately and
    # the command aborts with "Aborted!". Detect that up front so we can print
    # actionable guidance instead of a cryptic failure.
    if not sys.stdin.isatty():
        err(
            "`notebooklm login` needs an interactive terminal — it opens a browser\n"
            "and then waits for you to press ENTER in this shell. The current shell\n"
            "does not appear to be a TTY (common cause: running via the Claude Code\n"
            "`!` prefix, a pipe, or a non-interactive subprocess).\n\n"
            "Open a regular terminal window and run:\n\n"
            f"  {' '.join(cli)} login\n\n"
            "Sign in to Google in the Chromium window, wait for the NotebookLM\n"
            "homepage to load, then return to the terminal and press ENTER."
        )
        sys.exit(3)

    log("Launching `notebooklm login` — a browser window will open.")
    log("Sign in with your Google account, wait for the NotebookLM homepage,")
    log("then return to this terminal and press ENTER to save the session.")
    rc = run_checked(cli + ["login"], desc="notebooklm login")
    if rc != 0:
        err("`notebooklm login` failed. Rerun this script or run `notebooklm login` manually.")
        sys.exit(1)

    # ── Step 5: verify auth file ──────────────────────────────────────────────
    found = auth_file_exists()
    if found is None:
        err(
            "Login command returned 0 but no storage_state.json was found at any of:\n  "
            + "\n  ".join(str(p) for p in AUTH_PATHS)
            + "\nCheck `notebooklm auth check --test` for diagnostics."
        )
        sys.exit(2)

    log(f"NotebookLM session saved: {found}")
    log("Setup complete. You can now run `process_notebook.py <notebook_id>`.")


if __name__ == "__main__":
    main()
