from pathlib import Path
import html
import os
import shutil
import subprocess
import zipfile

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
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    _append_log(job, log_path, f"$ {_format_args(args)}")
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
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


def _local_binary(name: str, target_dir: Path) -> Path:
    source = _binary(name)
    target = target_dir / source.name
    copy_to(source, target)
    return target


def _text_report_to_html(report_path: Path, title: str, output_path: Path) -> Path:
    content = report_path.read_text(encoding="utf-8", errors="replace")
    escaped = html.escape(content)
    document = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ background: #0b0b0b; color: #e6edf3; font-family: Arial, sans-serif; margin: 32px; }}
    h1 {{ font-size: 24px; margin-bottom: 18px; }}
    pre {{ white-space: pre-wrap; background: #111; border: 1px solid #2a2a2a; border-radius: 8px; padding: 18px; line-height: 1.5; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <pre>{escaped}</pre>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")
    return output_path


def _zip_files(output_path: Path, files: list[Path], base_dir: Path | None = None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in files:
            if not file_path.exists() or not file_path.is_file():
                continue
            try:
                arcname = file_path.name if base_dir is None else file_path.relative_to(base_dir).as_posix()
            except ValueError:
                arcname = file_path.name
            archive.write(file_path, arcname)
    return output_path


def _extract_zip_members(archive_path: Path, raw_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_name = member.filename.replace("\\", "/")
            if member.is_dir() or member_name.startswith("/") or ".." in Path(member_name).parts:
                continue
            target = raw_dir / member_name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _extract_rar_members(archive_path: Path, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    unrar = shutil.which("unrar")
    seven_zip = shutil.which("7z") or shutil.which("7zz")
    bsdtar = shutil.which("bsdtar")
    if unrar:
        subprocess.run([unrar, "x", "-idq", "-o+", str(archive_path), str(raw_dir)], check=True)
        return
    if seven_zip:
        subprocess.run([seven_zip, "x", "-y", f"-o{raw_dir}", str(archive_path)], check=True)
        return
    if bsdtar:
        subprocess.run([bsdtar, "-xf", str(archive_path), "-C", str(raw_dir)], check=True)
        return
    raise RunnerError("Arquivo .rar requer unrar, 7z/7zz ou bsdtar instalado no servidor.")


def _extract_archive_once(archive_path: Path, raw_dir: Path) -> None:
    suffix = archive_path.suffix.lower()
    if suffix == ".zip":
        _extract_zip_members(archive_path, raw_dir)
    elif suffix == ".rar":
        _extract_rar_members(archive_path, raw_dir)
    else:
        raise RunnerError(f"Arquivo compactado nao suportado: {suffix}")


def _extract_nested_archives(raw_dir: Path, max_depth: int = 4) -> None:
    processed: set[Path] = set()
    for depth in range(max_depth):
        archives = [
            path for path in sorted(raw_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in {".zip", ".rar"} and path not in processed
        ]
        if not archives:
            return
        for archive in archives:
            processed.add(archive)
            nested_dir = archive.with_suffix(f"{archive.suffix}.extracted")
            shutil.rmtree(nested_dir, ignore_errors=True)
            nested_dir.mkdir(parents=True, exist_ok=True)
            try:
                _extract_archive_once(archive, nested_dir)
            except Exception as exc:
                raise RunnerError(f"Falha ao extrair compactado interno {archive.name}: {exc}") from exc
    remaining = [
        path.name for path in raw_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".zip", ".rar"} and path not in processed
    ]
    if remaining:
        raise RunnerError("Compactado possui aninhamento excessivo. Recompacte os arquivos com menos niveis.")


def _extract_archive_files(archive_path: Path, target_dir: Path, extension: str, output_name: str) -> list[Path]:
    raw_dir = target_dir / "archive_raw"
    output_dir = target_dir / output_name
    shutil.rmtree(raw_dir, ignore_errors=True)
    shutil.rmtree(output_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    _extract_archive_once(archive_path, raw_dir)
    _extract_nested_archives(raw_dir)

    extracted: list[Path] = []
    for source in sorted(raw_dir.rglob("*")):
        if not source.is_file() or source.suffix.lower() != extension:
            continue
        target = output_dir / source.name
        counter = 2
        while target.exists():
            target = output_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        copy_to(source, target)
        extracted.append(target)
    if not extracted:
        raise RunnerError(f"Nenhum arquivo {extension} encontrado no compactado.")
    return extracted


def _prepare_xml_input(uploaded_file: Path, paths: dict[str, Path]) -> tuple[Path, bool, int]:
    if uploaded_file.suffix.lower() in {".zip", ".rar"}:
        extracted = _extract_archive_files(uploaded_file, paths["processing"], ".xml", "xmls")
        return paths["processing"] / "xmls", True, len(extracted)
    suffix = uploaded_file.suffix.lower() or ".xml"
    processing_input = copy_to(uploaded_file, paths["processing"] / f"input{suffix}")
    return processing_input, False, 1


def _prepare_fdb_input(uploaded_file: Path, paths: dict[str, Path]) -> Path:
    if uploaded_file.suffix.lower() in {".zip", ".rar"}:
        extracted = _extract_archive_files(uploaded_file, paths["processing"], ".fdb", "fdb")
        if len(extracted) > 1:
            raise RunnerError("Compactado contem mais de uma base .fdb. Envie apenas uma base por reparo.")
        return extracted[0]
    return uploaded_file


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


def _firebird_env(binary_path: str) -> dict[str, str]:
    env = os.environ.copy()
    firebird_root = Path(binary_path).resolve().parent.parent
    lib_path = firebird_root / "lib"
    env["FIREBIRD"] = str(firebird_root)
    env["LD_LIBRARY_PATH"] = f"{lib_path}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
    return env


def _raise_firebird_ods_error(completed: subprocess.CompletedProcess[str]) -> None:
    output = completed.stdout or ""
    marker = "unsupported on-disk structure"
    if marker not in output.lower():
        return
    detail = next((line.strip() for line in output.splitlines() if marker in line.lower()), output.strip())
    raise RunnerError(
        f"{detail}. Instale ou configure GFIX_BIN/GBAK_BIN de uma versao Firebird compativel com a ODS da base."
    )


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
    finally:
        shutil.rmtree(paths["upload"], ignore_errors=True)


def _run_analise_xml(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    processing_input, is_batch, count = _prepare_xml_input(uploaded_file, paths)
    _run_command(job, paths["log"], [str(_binary("analise_xml_nfe")), str(processing_input)], paths["processing"])
    report = paths["processing"] / "SAIDA" / "relatorio.txt"
    if not report.exists():
        raise RunnerError("Relatorio nao foi gerado pelo modulo.")
    report_txt = copy_to(report, paths["result"] / "relatorio.txt")
    report_html = _text_report_to_html(report_txt, "Relatorio XML NF-e", paths["result"] / "relatorio.html")
    if is_batch:
        job.output_path = _zip_files(paths["result"] / "analise_xml_nfe_resultado.zip", [report_txt, report_html])
        job.result = f"Relatorio XML NF-e gerado para {count} arquivo(s)."
    else:
        job.output_path = _zip_files(paths["result"] / "analise_xml_nfe_resultado.zip", [report_txt, report_html])
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
    processing_input, is_batch, count = _prepare_xml_input(uploaded_file, paths)
    binary = _local_binary("autoexec_automation", paths["processing"])
    if is_batch:
        _run_command(job, paths["log"], [str(binary)], paths["processing"])
        output_dir = paths["processing"] / "SAIDA"
        outputs = sorted(output_dir.rglob("*.sql"))
        if not outputs:
            raise RunnerError("Nenhum autoexec.sql foi gerado.")
        package = paths["result"] / "autoexec_resultado.zip"
        files_to_zip: list[Path] = outputs[:]
        report_html = _text_report_to_html(paths["log"], "Relatorio Autoexec", paths["result"] / "relatorio.html")
        files_to_zip.append(report_html)
        job.output_path = _zip_files(package, files_to_zip, output_dir)
        job.result = f"Autoexec gerado para {len(outputs)} de {count} XML(s)."
        return

    _run_command(
        job,
        paths["log"],
        [str(binary), "--xml", str(processing_input), "--out", "job"],
        paths["processing"],
    )
    output = paths["processing"] / "SAIDA" / "job" / "autoexec.sql"
    if not output.exists():
        raise RunnerError("autoexec.sql nao foi gerado.")
    output_sql = copy_to(output, paths["result"] / "autoexec.sql")
    report_html = _text_report_to_html(paths["log"], "Relatorio Autoexec", paths["result"] / "relatorio.html")
    job.output_path = _zip_files(paths["result"] / "autoexec_resultado.zip", [output_sql, report_html])
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
    firebird_env = _firebird_env(gfix)
    _append_log(job, paths["log"], f"FIREBIRD={firebird_env['FIREBIRD']}")

    repair_input = _prepare_fdb_input(uploaded_file, paths)
    working = copy_to(repair_input, paths["processing"] / "working.fdb")
    backup_original = copy_to(repair_input, paths["backup"] / "original.fdb")
    backup_file = paths["processing"] / "repair.fbk"
    repaired = paths["result"] / "repaired.fdb"

    _append_log(job, paths["log"], f"Backup original criado: {backup_original.name}")
    auth = ["-user", settings.firebird_user, "-password", settings.firebird_password]

    validate = _run_command(
        job,
        paths["log"],
        [gfix, *auth, "-validate", str(working)],
        paths["processing"],
        check=False,
        env=firebird_env,
    )
    _raise_firebird_ods_error(validate)
    if validate.returncode != 0:
        _append_log(job, paths["log"], "Validacao retornou erro; tentando reparo com gfix -mend.")
    mend = _run_command(
        job,
        paths["log"],
        [gfix, *auth, "-mend", "-full", "-ignore", str(working)],
        paths["processing"],
        check=False,
        env=firebird_env,
    )
    _raise_firebird_ods_error(mend)
    if mend.returncode != 0:
        raise RunnerError(f"Comando falhou com codigo {mend.returncode}.")
    _run_command(job, paths["log"], [gbak, *auth, "-b", "-g", "-ignore", str(working), str(backup_file)], paths["processing"], env=firebird_env)
    _run_command(job, paths["log"], [gbak, *auth, "-c", str(backup_file), str(repaired)], paths["processing"], env=firebird_env)

    if not repaired.exists():
        raise RunnerError("Base reparada nao foi gerada.")
    job.output_path = repaired
    job.report_path = paths["log"]
    job.result = "Base Firebird reparada em copia isolada."
