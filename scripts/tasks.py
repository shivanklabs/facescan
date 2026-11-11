"""FaceScan developer tasks: `uv run tasks <name>`. Works in PowerShell and on Linux.

Tasks: setup, db-init, db-migrate, dev, lint, format, test, models, licences.
Standard library only.
"""

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import IO, TypedDict

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AI = ROOT / "ai"
FRONTEND = ROOT / "frontend"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
PY_PATHS = ("backend", "ai", "database", "scripts")
MYPY_PATHS = ("backend/app", "ai/src", "ai/benchmark", "scripts")
# .env values that are paths; made absolute (relative to the repo root) for child processes.
PATH_VARS = (
    "FACESCAN_CONFIG_ADAPTERS_PATH",
    "FACESCAN_CONFIG_POLICIES_DIR",
    "FACESCAN_STORAGE_ROOT",
    "FACESCAN_UPLOAD_SPOOL_DIR",
)
# .env secrets that are AES-256 keys: 32 random bytes, base64-encoded.
AES_256_KEY_VARS = ("FACESCAN_KEYS_MASTER_KEY", "FACESCAN_BENCH_KEY")
MIN_PSQL_MAJOR = 15
STOP_GRACE_SECONDS = 5.0
IS_WINDOWS = sys.platform == "win32"


class TaskError(Exception):
    """A task failed; the message is shown to the user without a traceback."""


# ---------------------------------------------------------------- helpers


def load_env_file(path: Path) -> dict[str, str]:
    """
    Purpose: parse a simple KEY=VALUE .env file (comments, blank lines, optional quotes).
    Inputs: path. Outputs: dict (empty if the file is missing). Side effects: reads the file.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def child_env() -> dict[str, str]:
    """
    Purpose: environment for child processes: .env values, overridden by the real environment.
    Outputs: env dict. Side effects: reads .env.
    """
    env = {**load_env_file(ENV_FILE), **os.environ}
    for key in PATH_VARS:
        value = env.get(key)
        if value and not Path(value).is_absolute():
            env[key] = str(ROOT / value)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def check_required_env_vars(
    env: dict[str, str], required_vars: Sequence[str], task_name: str
) -> None:
    """
    Purpose: preflight check that required .env variables exist and are non-empty.
    Inputs: env (dict of environment variables), required_vars (sequence of variable names),
        task_name (str task name for messaging).
    Outputs: None.
    Side effects: raises TaskError printing only the variable names (never values).
    """
    missing = [key for key in required_vars if not env.get(key, "").strip()]
    if missing:
        names = "\n".join(f"  - {key}" for key in missing)
        details = "\n".join(f"{key} is not set in .env." for key in missing)
        raise TaskError(
            f"Preflight check failed for `tasks {task_name}`.\n"
            f"The following required .env variable(s) are missing or empty:\n{names}\n"
            f"{details}\n"
            "Please configure them in your .env file."
        )


def get_test_db_url() -> str | None:
    """
    Purpose: resolve the database URL for integration and database tests from the environment
        or local .env file. Prefers FACESCAN_TEST_DB_URL, falling back to FACESCAN_DB_URL.
    Inputs: none.
    Outputs: database URL string or None if not configured.
    Side effects: reads .env file if it exists.
    """
    env_vars = load_env_file(ENV_FILE)
    return (
        os.environ.get("FACESCAN_TEST_DB_URL")
        or env_vars.get("FACESCAN_TEST_DB_URL")
        or os.environ.get("FACESCAN_DB_URL")
        or env_vars.get("FACESCAN_DB_URL")
    )


def test_env(*, test_db_url: str | None = None, skip_db: bool = False) -> dict[str, str]:
    """
    Purpose: minimal environment for test child processes. Passes process environment and
        the explicit test database URL without loading the rest of .env, keeping tests isolated
        from local development configuration.
    Inputs: test_db_url (str or None), skip_db (bool).
    Outputs: env dict.
    Side effects: none.
    """
    env = {**os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if test_db_url:
        env["FACESCAN_TEST_DB_URL"] = test_db_url
    if skip_db:
        env["FACESCAN_SKIP_DB"] = "1"
    return env


def check_db_reachable(db_url: str) -> None:
    """
    Purpose: verify that the PostgreSQL database host and port are accepting TCP connections.
    Inputs: db_url (database connection URL).
    Outputs: none.
    Side effects: opens and closes a temporary socket connection. Raises TaskError if unreachable.
    """
    cleaned = db_url
    if "://" in cleaned:
        cleaned = "http://" + cleaned.split("://", 1)[1]
    parsed = urllib.parse.urlsplit(cleaned)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=2.0):
            pass
    except OSError as exc:
        raise TaskError(
            f"PostgreSQL is not reachable at {host}:{port} ({exc}). "
            "Ensure PostgreSQL is running, or pass --skip-db to explicitly skip database tests."
        ) from None


def require_tool(name: str, hint: str) -> str:
    """Purpose: find an executable on PATH. Outputs: its path. Raises TaskError if missing."""
    found = shutil.which(name)
    if found is None:
        raise TaskError(f"{name} not found on PATH. {hint}")
    return found


def run(cmd: Sequence[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    """
    Purpose: run one command in the foreground, streaming its output.
    Inputs: argv, working directory, optional env. Outputs: none.
    Side effects: runs the process. Raises TaskError on a non-zero exit code.
    """
    shown = " ".join(Path(cmd[0]).stem if i == 0 else part for i, part in enumerate(cmd))
    print(f"\n$ {shown}   (in {cwd.relative_to(ROOT) if cwd != ROOT else '.'})", flush=True)
    result = subprocess.run(list(cmd), cwd=cwd, env=env, check=False)  # noqa: S603
    if result.returncode != 0:
        raise TaskError(f"command failed with exit code {result.returncode}: {shown}")


def python_tool(module: str, *args: str) -> list[str]:
    """Purpose: argv for a Python tool from this environment (`python -m <module>`)."""
    return [sys.executable, "-m", module, *args]


def npm() -> str:
    """Purpose: path of npm (npm.cmd on Windows). Raises TaskError if missing."""
    return require_tool("npm", "Install Node.js LTS from https://nodejs.org/")


# ---------------------------------------------------------------- setup


def _random_value(key: str) -> str:
    """Purpose: a random dev secret suited to the variable. Inputs: env var name."""
    if key in AES_256_KEY_VARS:
        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    if key in (
        "FACESCAN_STORAGE_SIGNING_KEY",
        "FACESCAN_AUTH_SESSION_SECRET",
    ):
        return secrets.token_urlsafe(48)
    return secrets.token_urlsafe(24)


def add_missing_env_keys() -> None:
    """
    Purpose: bring an existing .env up to date with keys added to .env.example later.
        Missing keys are appended (secrets get random values, others the example value);
        existing lines are never changed. DB password/URL are not generated here because they
        must match the role created by db-init.
    Side effects: may append lines to .env; prints key names only, never values.
    """
    existing = load_env_file(ENV_FILE)
    added: list[str] = []
    manual: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or line.lstrip().startswith("#") or key in existing:
            continue
        if key in ("FACESCAN_DB_PASSWORD", "FACESCAN_DB_URL"):
            manual.append(key)
        elif "CHANGE_ME" in value:
            added.append(f"{key}={_random_value(key)}")
        else:
            added.append(f"{key}={value}")
    if added:
        text = ENV_FILE.read_text(encoding="utf-8")
        prefix = "" if text.endswith("\n") or not text else "\n"
        with ENV_FILE.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(prefix + "\n".join(added) + "\n")
        names = ", ".join(entry.partition("=")[0] for entry in added)
        print(f".env exists; added missing keys: {names}")
    else:
        print(".env exists and has every key; leaving it unchanged.")
    if manual:
        print(f"Set these in .env by hand (must match db-init): {', '.join(manual)}")


def create_env_file() -> None:
    """
    Purpose: create .env from .env.example with random dev secrets in place of CHANGE_ME;
        if .env already exists, only append keys it is missing (add_missing_env_keys).
    Side effects: writes or appends to .env (never changes existing values, never prints
        secrets).
    """
    if ENV_FILE.exists():
        add_missing_env_keys()
        return
    db_password = secrets.token_urlsafe(24)
    lines: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if not sep or line.lstrip().startswith("#") or "CHANGE_ME" not in value:
            lines.append(line)
        elif key == "FACESCAN_EMAIL_SMTP_HOST":
            lines.append(f"{key}=localhost")
        elif key == "FACESCAN_DB_PASSWORD":
            lines.append(f"{key}={db_password}")
        elif key == "FACESCAN_DB_URL":
            lines.append(f"{key}={re.sub(r'CHANGE_ME[A-Z0-9_]*', db_password, value)}")
        else:
            lines.append(f"{key}={_random_value(key)}")
    with ENV_FILE.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("Created .env with random local secrets (git-ignored).")


def install_pre_commit_hooks() -> None:
    """
    Purpose: install the pre-commit git hooks, unless git uses a custom core.hooksPath (then
        pre-commit refuses; we leave the user's git config alone and print how to run checks).
    Side effects: writes .git/hooks/pre-commit, or prints a note.
    """
    git = require_tool("git", "Install Git: https://git-scm.com/")
    hooks_path = subprocess.run(  # noqa: S603
        [git, "config", "--get", "core.hooksPath"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if hooks_path:
        print(
            "\nNote: git core.hooksPath is set, so pre-commit hooks were not installed and your "
            "git config was left unchanged. Run `uv run tasks lint` before every commit; it runs "
            "all pre-commit hooks, including the gitleaks secret scan."
        )
        return
    run(python_tool("pre_commit", "install"))


def task_setup(_args: argparse.Namespace) -> None:
    """
    Purpose: first-time setup. uv has already synced the Python environment before this runs.
    Side effects: creates .env and var/storage, runs npm ci, installs pre-commit hooks.
    """
    create_env_file()
    env = child_env()
    Path(env.get("FACESCAN_STORAGE_ROOT", str(ROOT / "var" / "storage"))).mkdir(
        parents=True, exist_ok=True
    )
    run([npm(), "ci"], cwd=FRONTEND)
    install_pre_commit_hooks()
    if find_psql(env) is None:
        print("\nNote: psql was not found. Set FACESCAN_DB_PSQL_DIR in .env before db-init.")
    print("\nSetup done. Next: uv run tasks db-init, uv run tasks models, uv run tasks dev")


# ---------------------------------------------------------------- db-init


def find_pg_tool(name: str, env: dict[str, str]) -> str | None:
    """
    Purpose: locate a PostgreSQL client tool (psql, pg_dump, pg_restore):
        checks FACESCAN_DB_PSQL_DIR, then PATH, then Windows installs.
    Inputs: name (tool name without extension), env (environment dictionary).
    Outputs: path string or None.
    Side effects: queries filesystem and environment.
    """
    exe = f"{name}.exe" if IS_WINDOWS else name
    configured = env.get("FACESCAN_DB_PSQL_DIR", "").strip()
    if configured:
        candidate = Path(configured) / exe
        if candidate.is_file():
            return str(candidate)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "PostgreSQL"
        installs = sorted(
            base.glob(f"*/bin/{exe}"),
            key=lambda p: int(p.parent.parent.name) if p.parent.parent.name.isdigit() else 0,
        )
        if installs:
            return str(installs[-1])
    return None


def find_psql(env: dict[str, str]) -> str | None:
    """
    Purpose: locate psql: FACESCAN_DB_PSQL_DIR, then PATH, then the newest Windows install.
    Inputs: env (environment dictionary).
    Outputs: path or None.
    Side effects: reads the filesystem.
    """
    return find_pg_tool("psql", env)


def task_db_init(_args: argparse.Namespace) -> None:
    """
    Purpose: create the facescan role and database on the native PostgreSQL server.
    Side effects: runs database/setup/create_dev_database.sql through psql as the superuser;
        psql asks for the superuser password (or uses PGPASSWORD from the real environment).
    """
    if not ENV_FILE.exists():
        raise TaskError(".env is missing. Run: uv run tasks setup")
    env = child_env()
    psql = find_psql(env)
    if psql is None:
        raise TaskError("psql not found. Set FACESCAN_DB_PSQL_DIR in .env to PostgreSQL's bin.")
    version = subprocess.run(  # noqa: S603
        [psql, "--version"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"(\d+)(?:\.\d+)?", version)
    if match is None or int(match.group(1)) < MIN_PSQL_MAJOR:
        raise TaskError(f"psql {MIN_PSQL_MAJOR} or newer is required (found: {version.strip()})")
    run(
        [
            psql,
            "-X",
            "-h",
            env.get("FACESCAN_DB_HOST", "localhost"),
            "-p",
            env.get("FACESCAN_DB_PORT", "5432"),
            "-U",
            env.get("FACESCAN_DB_ADMIN_USER", "postgres"),
            "-d",
            "postgres",
            "-f",
            str(ROOT / "database" / "setup" / "create_dev_database.sql"),
        ],
        env=env,
    )


def task_db_migrate(_args: argparse.Namespace) -> None:
    """
    Purpose: apply database migrations (alembic upgrade head) as the facescan role.
    Side effects: creates or alters tables in FACESCAN_DB_NAME.
    """
    if not ENV_FILE.exists():
        raise TaskError(".env is missing. Run: uv run tasks setup")
    run(
        python_tool("alembic", "-c", str(ROOT / "database" / "alembic.ini"), "upgrade", "head"),
        env=child_env(),
    )


# ---------------------------------------------------------------- dev


@dataclass
class DevProcess:
    """One long-running dev process and its output pump."""

    name: str
    argv: list[str]
    cwd: Path
    proc: subprocess.Popen[str] | None = None
    pump: threading.Thread | None = None


def _pump_output(name: str, stream: IO[str], lock: threading.Lock) -> None:
    """Purpose: copy a child's output to ours, each line prefixed with the process name."""
    for line in iter(stream.readline, ""):
        with lock:
            sys.stdout.write(f"{name:<8}| {line}")
            sys.stdout.flush()
    stream.close()


def _start(process: DevProcess, env: dict[str, str], lock: threading.Lock) -> None:
    """
    Purpose: start a child in its own process group, so Ctrl+C reaches only this runner,
        which then stops the children in order.
    Side effects: spawns the process and a daemon output thread.
    """
    if sys.platform == "win32":
        proc = subprocess.Popen(  # noqa: S603
            process.argv,
            cwd=process.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            process.argv,
            cwd=process.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    assert proc.stdout is not None
    process.proc = proc
    process.pump = threading.Thread(
        target=_pump_output, args=(process.name, proc.stdout, lock), daemon=True
    )
    process.pump.start()


def _signal_stop(proc: subprocess.Popen[str]) -> None:
    """Purpose: ask a process group to stop (Ctrl+Break on Windows, SIGINT on Linux)."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGINT)
    except (ProcessLookupError, OSError):
        pass


def _force_kill(proc: subprocess.Popen[str]) -> None:
    """Purpose: kill a process and all its children (uvicorn reloader, node under npm)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)


def stop_all(processes: list[DevProcess]) -> None:
    """
    Purpose: stop every child: graceful signal, wait, then force-kill the whole tree.
    Side effects: terminates processes.
    """
    running = [p.proc for p in processes if p.proc is not None]
    for proc in running:
        _signal_stop(proc)
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    for proc in running:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    for proc in running:
        _force_kill(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=STOP_GRACE_SECONDS)
    for p in processes:
        if p.pump is not None:
            p.pump.join(timeout=1.0)


def _raise_keyboard_interrupt(_signum: int, _frame: FrameType | None) -> None:
    """Purpose: signal handler that reuses the Ctrl+C shutdown path. Side effects: raises."""
    raise KeyboardInterrupt


def _install_stop_handlers() -> None:
    """
    Purpose: treat SIGTERM (Linux, IDEs, CI) and Ctrl+Break (Windows) like Ctrl+C, so the
        children are always stopped. Side effects: replaces those signal handlers.
    """
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)


def task_dev(_args: argparse.Namespace) -> None:
    """
    Purpose: run the API (uvicorn --reload), the worker and Vite together until Ctrl+C (or
        SIGTERM / Ctrl+Break) or until one of them exits; then stop all three.
    Side effects: spawns three process trees; prints their prefixed output; installs signal
        handlers.
    """
    _install_stop_handlers()
    # Child output may contain characters the console/file encoding lacks (e.g. cp1252 when
    # redirected); replace them instead of crashing the output thread, which would block the child.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")
    if not ENV_FILE.exists():
        raise TaskError(".env is missing. Run: uv run tasks setup")
    env = child_env()
    processes = [
        DevProcess(
            "api",
            python_tool(
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--reload",
                "--reload-dir",
                str(BACKEND / "app"),
                "--reload-dir",
                str(AI / "src"),
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ),
            BACKEND,
        ),
        DevProcess("worker", python_tool("app.worker"), BACKEND),
        DevProcess("web", [npm(), "run", "dev"], FRONTEND),
    ]
    lock = threading.Lock()
    print("Starting api (:8000), worker and web (:5173). Press Ctrl+C to stop.", flush=True)
    exit_message = ""
    try:
        for p in processes:
            _start(p, env, lock)
        while not exit_message:
            for p in processes:
                if p.proc is not None and p.proc.poll() is not None:
                    exit_message = f"{p.name} exited with code {p.proc.returncode}"
                    break
            time.sleep(0.3)
    except KeyboardInterrupt:
        exit_message = ""
    finally:
        with lock:
            print("\nStopping api, worker and web...", flush=True)
        stop_all(processes)
    if exit_message:
        raise TaskError(exit_message)
    print("All dev processes stopped.")


def find_cloudflared() -> str | None:
    """
    Purpose: locate cloudflared binary: checks PATH, then standard Windows/winget install folders.
    Inputs: none.
    Outputs: path string or None.
    Side effects: checks filesystem and PATH.
    """
    on_path = shutil.which("cloudflared")
    if on_path:
        return on_path
    if sys.platform == "win32":
        exe = "cloudflared.exe"
        candidates: list[Path] = []
        for pf_key in ("PROGRAMFILES", "ProgramFiles(x86)", "ProgramW6432"):
            pf = os.environ.get(pf_key)
            if pf:
                candidates.append(Path(pf) / "cloudflared" / exe)
        candidates.extend(
            [
                Path(r"C:\Program Files\cloudflared") / exe,
                Path(r"C:\Program Files (x86)\cloudflared") / exe,
            ]
        )
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            lad_path = Path(local_app_data)
            candidates.append(lad_path / "Microsoft" / "WinGet" / "Links" / exe)
            winget_pkgs = lad_path / "Microsoft" / "WinGet" / "Packages"
            if winget_pkgs.is_dir():
                candidates.extend(winget_pkgs.glob(f"Cloudflare.cloudflared_*/{exe}"))
                candidates.extend(winget_pkgs.glob(f"*/{exe}"))

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def task_public(_args: argparse.Namespace) -> None:
    """
    Purpose: phone test mode for real guests.
      1. Builds the frontend for production (npm run build).
      2. Starts cloudflared Quick Tunnel (http://127.0.0.1:8000).
      3. Reads the https://*.trycloudflare.com URL.
      4. Configures public URL, WebAuthn RP ID and origin, and allowed CORS/hosts.
      5. Runs the API and worker on 127.0.0.1 with Silent-Face liveness and Gmail SMTP.
      6. Prints the public URL clearly.
    Inputs: _args (argparse.Namespace, unused).
    Outputs: None.
    Side effects: builds frontend, spawns tunnel, api, and worker; stops on Ctrl+C.
    """
    _install_stop_handlers()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")
    if not ENV_FILE.exists():
        raise TaskError(".env is missing. Run: uv run tasks setup")

    env = child_env()
    check_required_env_vars(
        env,
        [
            "FACESCAN_AUTH_ORGANISER_EMAILS",
            "FACESCAN_AUTH_ADMIN_EMAILS",
            "FACESCAN_EMAIL_ADAPTER",
            "FACESCAN_EMAIL_SMTP_USER",
            "FACESCAN_EMAIL_SMTP_PASSWORD",
        ],
        "public",
    )

    cloudflared_bin = find_cloudflared()
    if not cloudflared_bin:
        raise TaskError(
            "cloudflared is not installed or not in PATH.\n"
            "Please install it via winget (Windows): winget install Cloudflare.cloudflared\n"
            "or download from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        )

    print("Building frontend for production...", flush=True)
    build_env = child_env()
    build_env["VITE_FACESCAN_API_URL"] = ""
    run([npm(), "run", "build"], cwd=FRONTEND, env=build_env)

    print("Starting Cloudflare Quick Tunnel on 127.0.0.1:8000...", flush=True)
    tunnel_cmd = [cloudflared_bin, "tunnel", "--url", "http://127.0.0.1:8000"]
    tunnel_proc = subprocess.Popen(  # noqa: S603
        tunnel_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    tunnel_url = ""
    start_time = time.time()
    assert tunnel_proc.stderr is not None
    for line in tunnel_proc.stderr:
        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if match:
            tunnel_url = match.group(0)
            break
        if time.time() - start_time > 30:
            tunnel_proc.terminate()
            raise TaskError("Timed out waiting for Cloudflare tunnel URL (30s).")

    if not tunnel_url:
        tunnel_proc.terminate()
        raise TaskError("Failed to extract Cloudflare tunnel URL.")

    host = urllib.parse.urlparse(tunnel_url).netloc

    print("\n" + "=" * 78, flush=True)
    print("  FACE-SCAN PHONE TEST MODE READY", flush=True)
    print(f"  Public Tunnel URL:  {tunnel_url}", flush=True)
    print("=" * 78, flush=True)
    print("  Important Instructions:", flush=True)
    print(f"  1. Open {tunnel_url} on your phone to test the guest experience.", flush=True)
    print("  2. The tunnel URL changes on every restart.", flush=True)
    print("     The organiser must register a new passkey after each restart.", flush=True)
    print("  3. Magic links are sent via Gmail SMTP to the recipient's email address.", flush=True)
    print("  4. Press Ctrl+C to stop the API, worker, and tunnel.", flush=True)
    print("=" * 78 + "\n", flush=True)

    env = child_env()
    env["FACESCAN_ENV"] = "public"
    env["FACESCAN_PUBLIC_BASE_URL"] = tunnel_url
    env["FACESCAN_AUTH_RP_ORIGIN"] = tunnel_url
    env["FACESCAN_AUTH_RP_ID"] = host
    env["FACESCAN_STORAGE_PUBLIC_BASE_URL"] = tunnel_url
    env["FACESCAN_LIVENESS_ADAPTER"] = "silent_face"
    env["FACESCAN_EMAIL_ADAPTER"] = "smtp_gmail"

    processes = [
        DevProcess(
            "api",
            python_tool(
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ),
            BACKEND,
        ),
        DevProcess("worker", python_tool("app.worker"), BACKEND),
    ]

    lock = threading.Lock()
    exit_message = ""
    try:
        for p in processes:
            _start(p, env, lock)
        while not exit_message:
            for p in processes:
                if p.proc is not None and p.proc.poll() is not None:
                    exit_message = f"{p.name} exited with code {p.proc.returncode}"
                    break
            if tunnel_proc.poll() is not None:
                exit_message = f"cloudflared tunnel exited with code {tunnel_proc.returncode}"
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        exit_message = ""
    finally:
        with lock:
            print("\nStopping api, worker and tunnel...", flush=True)
        stop_all(processes)
        try:
            tunnel_proc.terminate()
            tunnel_proc.wait(timeout=3)
        except Exception:
            tunnel_proc.kill()
    if exit_message:
        raise TaskError(exit_message)
    print("Public test mode stopped cleanly.")


# ---------------------------------------------------------------- lint / format / test


def task_lint(args: argparse.Namespace) -> None:
    """
    Purpose: ruff + mypy (Python), eslint + tsc (frontend), then every pre-commit hook on all
        files (gitleaks secret scan of staged and unstaged changes, licence check, .env block).
        Run it before each commit: the hooks are not installed when git uses core.hooksPath.
    Inputs: --only python|frontend limits the run (pre-commit runs with the Python part).
    Side effects: the first pre-commit run downloads and builds the pinned gitleaks.
    """
    if args.only in (None, "python"):
        run(python_tool("ruff", "check", *PY_PATHS))
        run(python_tool("ruff", "format", "--check", *PY_PATHS))
        run(python_tool("mypy", *MYPY_PATHS))
    if args.only in (None, "frontend"):
        run([npm(), "run", "lint"], cwd=FRONTEND)
        run([npm(), "run", "typecheck"], cwd=FRONTEND)
    if args.only in (None, "python"):
        run(python_tool("pre_commit", "run", "--all-files", "--show-diff-on-failure"))


def task_format(_args: argparse.Namespace) -> None:
    """Purpose: ruff format + ruff --fix + prettier. Side effects: rewrites source files."""
    run(python_tool("ruff", "format", *PY_PATHS))
    run(python_tool("ruff", "check", "--fix", *PY_PATHS))
    run([npm(), "run", "format"], cwd=FRONTEND)


def task_test(args: argparse.Namespace) -> None:
    """
    Purpose: run test suites (backend, ai, frontend). Runs all requested suites even if an
        earlier one fails, collecting errors and reporting all failures together at the end.
        Ensures database tests receive an explicit test database URL without loading the rest
        of .env, and never skip silently unless --skip-db is explicitly specified.
    Inputs: args with optional .only ("python", "frontend", "backend", "ai") and .skip_db (bool).
    Outputs: none.
    Side effects: runs pytest and vitest child processes. Raises TaskError if any suite failed.
    """
    failures: list[str] = []
    skip_db = getattr(args, "skip_db", False)
    test_db_url = None if skip_db else get_test_db_url()

    if args.only in (None, "python", "backend"):
        if not skip_db:
            if not test_db_url:
                raise TaskError(
                    "Database tests require a database URL. Set FACESCAN_TEST_DB_URL or "
                    "FACESCAN_DB_URL in .env or your environment, or pass --skip-db to explicitly "
                    "skip database tests."
                )
            check_db_reachable(test_db_url)
        backend_args = ["--skip-db"] if skip_db else []
        backend_env = test_env(test_db_url=test_db_url, skip_db=skip_db)
        try:
            run(python_tool("pytest", *backend_args), cwd=BACKEND, env=backend_env)
        except TaskError as exc:
            failures.append(f"backend tests: {exc}")
    if args.only in (None, "python", "ai"):
        ai_env = test_env()
        try:
            run(python_tool("pytest"), cwd=AI, env=ai_env)
        except TaskError as exc:
            failures.append(f"AI tests: {exc}")
    if args.only in (None, "frontend"):
        try:
            run([npm(), "test"], cwd=FRONTEND)
        except TaskError as exc:
            failures.append(f"frontend tests: {exc}")
    if failures:
        raise TaskError("; ".join(failures))


def task_models(_args: argparse.Namespace) -> None:
    """Purpose: download model weights and verify SHA-256. Side effects: writes ai/models/."""
    run([sys.executable, str(AI / "scripts" / "download_models.py")])


def task_licences(_args: argparse.Namespace) -> None:
    """Purpose: fail on forbidden models or packages. Side effects: none."""
    run([sys.executable, str(ROOT / "scripts" / "check_licences.py")])


# ---------------------------------------------------------------- entry point


class BackupFileEntry(TypedDict):
    """File metadata in backup manifest."""

    path: str
    bytes: int
    sha256: str


def _is_relative_to(path: Path, base: Path) -> bool:
    """
    Purpose: test whether path is relative to base.
    Inputs: path (Path), base (Path).
    Outputs: bool.
    Side effects: none.
    """
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def task_backup(_args: argparse.Namespace) -> None:
    """
    Purpose: create an atomic, timestamped backup of the PostgreSQL database and photo storage.
      1. Reads FACESCAN_BACKUP_DIR from .env (must be outside repo root and storage directory).
      2. Dumps database using pg_dump -Fc with row security enabled.
      3. Copies photo storage directory (excluding temporary spool and locks; never copying .env).
      4. Generates manifest.json with file count, total size, and SHA-256 digests.
      5. Verifies backup: re-checks SHA-256 digests and runs pg_restore --list.
      6. Leaves original storage folder and database untouched.
    Inputs: _args (argparse.Namespace, unused).
    Outputs: None.
    Side effects: writes timestamped backup folder under FACESCAN_BACKUP_DIR.
    """
    if not ENV_FILE.exists():
        raise TaskError(".env is missing. Run: uv run tasks setup")
    env = child_env()

    check_required_env_vars(
        env,
        [
            "FACESCAN_BACKUP_DIR",
        ],
        "backup",
    )

    backup_dir_val = env.get("FACESCAN_BACKUP_DIR", "").strip()

    backup_dir = (
        Path(backup_dir_val) if Path(backup_dir_val).is_absolute() else (ROOT / backup_dir_val)
    ).resolve()

    if backup_dir == ROOT or _is_relative_to(backup_dir, ROOT):
        raise TaskError(
            f"FACESCAN_BACKUP_DIR ({backup_dir}) must be outside the repository root ({ROOT})."
        )

    storage_root_str = env.get("FACESCAN_STORAGE_ROOT", "var/storage")
    storage_root = (
        Path(storage_root_str)
        if Path(storage_root_str).is_absolute()
        else (ROOT / storage_root_str)
    ).resolve()

    if (
        backup_dir == storage_root
        or _is_relative_to(backup_dir, storage_root)
        or _is_relative_to(storage_root, backup_dir)
    ):
        raise TaskError(
            f"FACESCAN_BACKUP_DIR ({backup_dir}) must be outside the storage directory "
            f"({storage_root})."
        )

    pg_dump = find_pg_tool("pg_dump", env)
    if not pg_dump:
        raise TaskError(
            "pg_dump not found. Set FACESCAN_DB_PSQL_DIR in .env or add PostgreSQL bin to PATH."
        )

    pg_restore = find_pg_tool("pg_restore", env)
    if not pg_restore:
        raise TaskError(
            "pg_restore not found. Set FACESCAN_DB_PSQL_DIR in .env or add PostgreSQL bin to PATH."
        )

    db_url = env.get("FACESCAN_DB_URL", "").strip()
    if not db_url:
        raise TaskError("FACESCAN_DB_URL is not set in .env.")
    clean_db_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%SZ")
    dest_dir = backup_dir / f"backup_{timestamp}"
    dest_dir.mkdir(parents=True, exist_ok=False)

    print(f"Creating backup in {dest_dir}...", flush=True)

    # 1. Database dump
    dump_file = dest_dir / "database.dump"
    print("Dumping PostgreSQL database (pg_dump -Fc)...", flush=True)
    dump_cmd = [pg_dump, "-Fc", "--enable-row-security", "-d", clean_db_url, "-f", str(dump_file)]
    dump_res = subprocess.run(  # noqa: S603
        dump_cmd, capture_output=True, text=True, check=False
    )
    if dump_res.returncode != 0:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise TaskError(
            f"pg_dump failed with exit code {dump_res.returncode}: {dump_res.stderr.strip()}"
        )

    # 2. Storage directory copy
    dest_storage = dest_dir / "storage"
    print("Copying photo storage folder...", flush=True)
    if storage_root.exists() and any(storage_root.iterdir()):
        shutil.copytree(
            storage_root,
            dest_storage,
            ignore=shutil.ignore_patterns(".env*", "*.tmp", "*.lock"),
        )
    else:
        dest_storage.mkdir(parents=True, exist_ok=True)

    # Invariant: Never copy .env into backup
    for item in dest_dir.rglob("*"):
        if item.is_file() and (item.name == ".env" or item.name.startswith(".env.")):
            item.unlink()

    # 3. Manifest creation
    print("Writing backup manifest...", flush=True)
    manifest_files: list[BackupFileEntry] = []
    for p in sorted(dest_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            rel_posix = p.relative_to(dest_dir).as_posix()
            size = p.stat().st_size
            h = hashlib.sha256()
            with p.open("rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            manifest_files.append(
                {
                    "path": rel_posix,
                    "bytes": size,
                    "sha256": h.hexdigest(),
                }
            )

    total_bytes = sum(entry["bytes"] for entry in manifest_files)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "file_count": len(manifest_files),
        "total_bytes": total_bytes,
        "files": manifest_files,
    }
    manifest_path = dest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # 4. Verification before finishing
    print("Verifying backup integrity...", flush=True)
    for mf in manifest_files:
        item_path = dest_dir / mf["path"]
        if not item_path.is_file():
            raise TaskError(f"Verification failed: missing file {mf['path']}")
        h = hashlib.sha256()
        with item_path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        actual_sha = h.hexdigest()
        if actual_sha != mf["sha256"]:
            raise TaskError(
                f"Verification failed: SHA-256 mismatch for {mf['path']} "
                f"(expected {mf['sha256']}, got {actual_sha})"
            )

    restore_cmd = [pg_restore, "--list", str(dump_file)]
    restore_res = subprocess.run(  # noqa: S603
        restore_cmd, capture_output=True, text=True, check=False
    )
    if restore_res.returncode != 0:
        raise TaskError(
            f"Verification failed: pg_restore --list failed: {restore_res.stderr.strip()}"
        )

    print("\n" + "=" * 70)
    print("  BACKUP VERIFIED AND COMPLETE")
    print(f"  Target:     {dest_dir}")
    print(f"  Files:      {len(manifest_files)} (all SHA-256 verified)")
    print(f"  Total size: {total_bytes / (1024 * 1024):.2f} MB")
    print(f"  DB dump:    {dump_file.name} (pg_restore format valid)")
    print("=" * 70 + "\n")


TASKS: dict[str, tuple[Callable[[argparse.Namespace], None], str]] = {
    "setup": (task_setup, "first-time setup: .env, npm ci, pre-commit, var/storage"),
    "db-init": (task_db_init, "create the facescan role and database (asks for the PG password)"),
    "db-migrate": (task_db_migrate, "apply database migrations (alembic upgrade head)"),
    "dev": (task_dev, "run api, worker and Vite together; Ctrl+C stops all three"),
    "lint": (task_lint, "ruff + mypy, eslint + tsc, pre-commit (gitleaks); run before commits"),
    "format": (task_format, "ruff format/fix + prettier"),
    "test": (task_test, "pytest (backend, ai) + vitest"),
    "models": (task_models, "download model weights and verify SHA-256"),
    "licences": (task_licences, "fail on forbidden models or packages"),
    "public": (
        task_public,
        "phone test mode: production frontend build, Cloudflare tunnel, api, worker",
    ),
    "backup": (
        task_backup,
        "backup database (pg_dump -Fc) and photo storage to FACESCAN_BACKUP_DIR with verification",
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    """
    Purpose: CLI entry point (`uv run tasks <name>`).
    Inputs: argv. Outputs: exit code. Side effects: runs the chosen task.
    """
    parser = argparse.ArgumentParser(prog="tasks", description="FaceScan developer tasks")
    sub = parser.add_subparsers(dest="task", required=True, metavar="<task>")
    for name, (_, help_text) in TASKS.items():
        task_parser = sub.add_parser(name, help=help_text)
        if name == "lint":
            task_parser.add_argument("--only", choices=("python", "frontend"), default=None)
        elif name == "test":
            task_parser.add_argument(
                "--only", choices=("python", "frontend", "backend", "ai"), default=None
            )
            task_parser.add_argument(
                "--skip-db",
                "--skip-database",
                dest="skip_db",
                action="store_true",
                default=False,
                help="explicitly skip database and security tests that require PostgreSQL",
            )
    args = parser.parse_args(argv)
    try:
        TASKS[args.task][0](args)
    except TaskError as exc:
        print(f"\ntasks {args.task}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
