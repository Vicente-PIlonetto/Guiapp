from __future__ import annotations

from pathlib import Path
import os
import platform
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LOG_DIR = ROOT / "storage" / "logs"
PID_FILE = ROOT / "storage" / "server.pid"
ENV_FILE = ROOT / ".env"


def packaged_exe_path() -> Path | None:
    if platform.system().lower() != "windows":
        return None
    candidates = [
        ROOT / "GUINAPP.exe",
        ROOT / "dist" / "GUINAPP" / "GUINAPP.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    content = "\n".join(f"{key}={value}" for key, value in sorted(values.items())) + "\n"
    ENV_FILE.write_text(content, encoding="utf-8")


def server_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid


def is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_server() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    pid = server_pid()
    if is_running(pid):
        print(f"Servidor ja esta em execucao: PID {pid}")
        return
    env_values = read_env()
    env = os.environ.copy()
    env.update(env_values)
    exe_path = packaged_exe_path()
    host = env.get("APP_HOST", "127.0.0.1" if exe_path else "0.0.0.0")
    port = env.get("APP_PORT", "8000")
    log_file = (LOG_DIR / "server.log").open("a", encoding="utf-8")
    if exe_path:
        package_env = exe_path.parent / ".env"
        package_values = dict(env_values)
        package_values.setdefault("APP_HOST", host)
        package_values.setdefault("APP_PORT", port)
        package_values.setdefault("STORAGE_DIR", "storage")
        content = "\n".join(f"{key}={value}" for key, value in sorted(package_values.items())) + "\n"
        package_env.write_text(content, encoding="utf-8")
        process = subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
    else:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", host, "--port", port],
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    print(f"Servidor iniciado em http://localhost:{port} (PID {process.pid})")


def stop_server() -> None:
    pid = server_pid()
    if not is_running(pid):
        print("Servidor nao esta em execucao.")
        PID_FILE.unlink(missing_ok=True)
        return
    assert pid is not None
    os.kill(pid, signal.SIGTERM)
    time.sleep(1)
    PID_FILE.unlink(missing_ok=True)
    print("Servidor parado.")


def restart_server() -> None:
    stop_server()
    start_server()


def configure() -> None:
    values = read_env()
    port = input(f"Porta [{values.get('APP_PORT', '8000')}]: ").strip()
    uploads = input(f"Diretorio storage [{values.get('STORAGE_DIR', 'storage')}]: ").strip()
    if port:
        values["APP_PORT"] = port
    if uploads:
        values["STORAGE_DIR"] = uploads
    if "APP_HOST" not in values:
        values["APP_HOST"] = "0.0.0.0"
    write_env(values)
    print(".env atualizado.")


def compile_modules() -> None:
    subprocess.run(["make", "-C", "modules"], cwd=str(ROOT), check=False)


def check_dependencies() -> None:
    deps = ["python", "gcc", "make", "node", "npm", "gfix", "gbak", "tailscale"]
    for dep in deps:
        found = shutil.which(dep)
        print(f"{dep:10} {'OK ' + found if found else 'NAO ENCONTRADO'}")


def show_logs() -> None:
    log_path = LOG_DIR / "server.log"
    if not log_path.exists():
        print("Nenhum log do servidor encontrado.")
        return
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-40:]:
        print(line)


def show_status() -> None:
    values = read_env()
    port = values.get("APP_PORT", "8000")
    pid = server_pid()
    print(f"Servidor: {'rodando' if is_running(pid) else 'parado'}")
    print(f"PID: {pid or '-'}")
    print(f"URL local: http://localhost:{port}")
    print(f"Tailscale Funnel: tailscale funnel {port}")


def menu() -> None:
    actions = {
        "1": ("Iniciar servidor", start_server),
        "2": ("Parar servidor", stop_server),
        "3": ("Reiniciar servidor", restart_server),
        "4": ("Status", show_status),
        "5": ("Configurar porta/storage", configure),
        "6": ("Ultimos logs", show_logs),
        "7": ("Compilar modulos C", compile_modules),
        "8": ("Verificar dependencias", check_dependencies),
        "0": ("Sair", None),
    }
    while True:
        print("\nGUINAPP TUI")
        for key, (label, _) in actions.items():
            print(f"{key}. {label}")
        choice = input("> ").strip()
        if choice == "0":
            return
        action = actions.get(choice)
        if not action:
            print("Opcao invalida.")
            continue
        action[1]()


if __name__ == "__main__":
    menu()
