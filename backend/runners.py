from pathlib import Path
import os
import shutil
import subprocess

from backend.config import get_settings
from backend.models import Job, ModuleDefinition
from backend.storage import copy_to, job_dirs


class RunnerError(RuntimeError):
    pass


def _append_log(job: Job, log_path: Path, message: str) -> None:
    job.add_log(message)
    with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(message.rstrip() + "\n")


def _format_args(args: list[str]) -> str:
    redacted: list[str] = []
    hide_next = False
    for arg in args:
        if hide_next:
            redacted.append("********")
            hide_next = False
            continue
        redacted.append(arg)
        if arg.lower() in {"-password", "-pass"}:
            hide_next = True
    return " ".join(redacted)


def _run_command(
    job: Job,
    log_path: Path,
    args: list[str],
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    _append_log(job, log_path, f"$ {_format_args(args)}")
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=None,
        check=False,
    )
    if completed.stdout:
        for line in completed.stdout.splitlines()[-40:]:
            _append_log(job, log_path, line)
    if check and completed.returncode != 0:
        raise RunnerError(f"Comando falhou com codigo {completed.returncode}.")
    return completed


def _binary(name: str) -> Path:
    path = get_settings().asset_dir / "modules" / "build" / name
    if path.exists():
        return path
    windows_path = path.with_suffix(".exe")
    if windows_path.exists():
        return windows_path
    if not path.exists():
        raise RunnerError(f"Binario nao encontrado: {path}. Execute `make modules`.")
    return path


def _firebird_binary(setting_value: str, binary_name: str) -> str | None:
    configured = Path(setting_value)
    if configured.exists():
        return str(configured)

    from_path = shutil.which(setting_value)
    if from_path:
        return from_path

    settings = get_settings()
    local_filename = f"{binary_name}.exe" if os.name == "nt" else binary_name
    candidates = [
        settings.asset_dir / "firebird" / local_filename,
        settings.asset_dir / "modules" / "Reparo de base" / local_filename,
        settings.root_dir / "firebird" / local_filename,
        settings.root_dir / "modules" / "Reparo de base" / local_filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def run_job(job: Job, module: ModuleDefinition, uploaded_file: Path) -> None:
    paths = job_dirs(job.id)
    log_path = paths["log"]
    job.status = "processing"
    _append_log(job, log_path, "Job iniciado.")
    try:
        if module.runner == "analise_xml_nfe":
            _run_analise_xml(job, uploaded_file, paths)
        elif module.runner == "analise_log_nfse":
            _run_analise_log(job, uploaded_file, paths)
        elif module.runner == "autoexec_automation":
            _run_autoexec(job, uploaded_file, paths)
        elif module.runner == "firebird_repair":
            _run_firebird_repair(job, uploaded_file, paths)
        else:
            raise RunnerError("Modulo sem runner executavel.")
        job.status = "completed"
        if not job.result:
            job.result = "Processamento concluido."
        _append_log(job, log_path, "Job concluido.")
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        if not job.report_path and log_path.exists():
            job.report_path = log_path
        _append_log(job, log_path, f"ERRO: {exc}")


def _run_analise_xml(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    processing_input = copy_to(uploaded_file, paths["processing"] / "input.xml")
    _run_command(job, paths["log"], [str(_binary("analise_xml_nfe")), str(processing_input)], paths["processing"])
    report = paths["processing"] / "SAIDA" / "relatorio.txt"
    if not report.exists():
        raise RunnerError("Relatorio nao foi gerado pelo modulo.")
    job.report_path = copy_to(report, paths["result"] / "relatorio.txt")
    job.result = "Relatorio XML NF-e gerado."


def _run_analise_log(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    copy_to(uploaded_file, paths["processing"] / "system.log")
    _run_command(job, paths["log"], [str(_binary("analise_log_nfse"))], paths["processing"])
    report = paths["processing"] / "Saida" / "resultado.txt"
    if not report.exists():
        raise RunnerError("Resultado nao foi gerado pelo modulo.")
    job.report_path = copy_to(report, paths["result"] / "resultado.txt")
    job.result = "Resumo de log NFS-e gerado."


def _run_autoexec(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    processing_input = copy_to(uploaded_file, paths["processing"] / "input.xml")
    _run_command(
        job,
        paths["log"],
        [str(_binary("autoexec_automation")), "--xml", str(processing_input), "--out", "job"],
        paths["processing"],
    )
    output = paths["processing"] / "SAIDA" / "job" / "autoexec.sql"
    if not output.exists():
        raise RunnerError("autoexec.sql nao foi gerado.")
    job.output_path = copy_to(output, paths["result"] / "autoexec.sql")
    job.report_path = job.output_path
    job.result = "autoexec.sql gerado."


def _run_firebird_repair(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    settings = get_settings()
    gfix = _firebird_binary(settings.gfix_bin, "gfix")
    gbak = _firebird_binary(settings.gbak_bin, "gbak")
    if not gfix or not gbak:
        raise RunnerError(
            "Dependencias Firebird nao encontradas. Configure GFIX_BIN/GBAK_BIN "
            "ou instale as ferramentas Firebird nativas do sistema."
        )

    job.report_path = paths["log"]
    _append_log(job, paths["log"], f"Usando gfix: {gfix}")
    _append_log(job, paths["log"], f"Usando gbak: {gbak}")

    working = copy_to(uploaded_file, paths["processing"] / "working.fdb")
    backup_original = copy_to(uploaded_file, paths["backup"] / "original.fdb")
    backup_file = paths["processing"] / "repair.fbk"
    repaired = paths["result"] / "repaired.fdb"

    _append_log(job, paths["log"], f"Backup original criado: {backup_original.name}")
    auth = ["-user", settings.firebird_user, "-password", settings.firebird_password]

    validate = _run_command(job, paths["log"], [gfix, *auth, "-validate", str(working)], paths["processing"], check=False)
    if validate.returncode != 0:
        _append_log(job, paths["log"], "Validacao retornou erro; tentando reparo com gfix -mend.")
    _run_command(job, paths["log"], [gfix, *auth, "-mend", "-full", "-ignore", str(working)], paths["processing"])
    _run_command(job, paths["log"], [gbak, *auth, "-b", "-g", "-ignore", str(working), str(backup_file)], paths["processing"])
    _run_command(job, paths["log"], [gbak, *auth, "-c", str(backup_file), str(repaired)], paths["processing"])

    if not repaired.exists():
        raise RunnerError("Base reparada nao foi gerada.")
    job.output_path = repaired
    job.report_path = paths["log"]
    job.result = "Base Firebird reparada em copia isolada."
