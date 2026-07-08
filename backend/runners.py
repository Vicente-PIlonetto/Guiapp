from pathlib import Path
import html
import os
import shutil
import subprocess
import textwrap
import zipfile

from PIL import Image, ImageOps, UnidentifiedImageError

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


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_report_to_pdf(report_path: Path, title: str, output_path: Path) -> Path:
    content = report_path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = [title, ""]
    for original_line in content.splitlines():
        wrapped = textwrap.wrap(original_line, width=94, replace_whitespace=False, drop_whitespace=False)
        lines.extend(wrapped or [""])

    lines_per_page = 46
    pages = [lines[index:index + lines_per_page] for index in range(0, len(lines), lines_per_page)] or [[]]
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_object(b"")
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    page_ids: list[int] = []

    for page_lines in pages:
        commands = ["BT", f"/F1 10 Tf", "50 790 Td", "14 TL"]
        for line in page_lines:
            safe_line = line.encode("cp1252", errors="replace").decode("cp1252")
            commands.append(f"({_pdf_escape(safe_line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("cp1252", errors="replace")
        content_id = add_object(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add_object(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
        )
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        handle.write(b"%PDF-1.4\n")
        offsets = [0]
        for index, payload in enumerate(objects, start=1):
            offsets.append(handle.tell())
            handle.write(f"{index} 0 obj\n".encode("ascii"))
            handle.write(payload)
            handle.write(b"\nendobj\n")
        xref_offset = handle.tell()
        handle.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        handle.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            handle.write(f"{offset:010d} 00000 n \n".encode("ascii"))
        handle.write(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )
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


def _cleanup_transient_paths(paths: dict[str, Path], include_result: bool = False) -> None:
    for key in ("upload", "processing", "backup"):
        shutil.rmtree(paths[key], ignore_errors=True)
    if include_result:
        shutil.rmtree(paths["result"], ignore_errors=True)


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
    shutil.rmtree(raw_dir, ignore_errors=True)
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
    bin_path = firebird_root / "bin"
    env["FIREBIRD"] = str(firebird_root)
    env["LD_LIBRARY_PATH"] = f"{lib_path}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
    env["PATH"] = f"{bin_path}:{env.get('PATH', '')}".rstrip(":")
    return env


def _configure_firebird_runtime(env: dict[str, str], paths: dict[str, Path]) -> None:
    runtime_dir = paths["processing"] / "firebird_runtime"
    lock_dir = runtime_dir / "lock"
    tmp_dir = runtime_dir / "tmp"
    lock_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["FIREBIRD_LOCK"] = str(lock_dir)
    env["TMPDIR"] = str(tmp_dir)
    env["TEMP"] = str(tmp_dir)
    env["TMP"] = str(tmp_dir)


def _raise_firebird_ods_error(completed: subprocess.CompletedProcess[str]) -> None:
    output = completed.stdout or ""
    marker = "unsupported on-disk structure"
    if marker not in output.lower():
        return
    detail = next((line.strip() for line in output.splitlines() if marker in line.lower()), output.strip())
    raise RunnerError(
        f"{detail}. Instale ou configure GFIX_BIN/GBAK_BIN de uma versao Firebird compativel com a ODS da base."
    )


def _raise_no_space_error(completed: subprocess.CompletedProcess[str], target_path: Path) -> None:
    output = (completed.stdout or "").lower()
    if "no space left on device" not in output:
        return
    usage = shutil.disk_usage(target_path.parent)
    free_gb = usage.free / (1024 ** 3)
    raise RunnerError(
        f"Espaco insuficiente no servidor para gerar a base reparada. "
        f"Livre em {target_path.parent}: {free_gb:.2f} GB."
    )


def _ensure_restore_space(source_database: Path, target_path: Path) -> None:
    usage = shutil.disk_usage(target_path.parent)
    required = int(source_database.stat().st_size * 1.25) + (512 * 1024 * 1024)
    if usage.free >= required:
        return
    free_gb = usage.free / (1024 ** 3)
    required_gb = required / (1024 ** 3)
    raise RunnerError(
        f"Espaco insuficiente no servidor antes do restore. "
        f"Livre: {free_gb:.2f} GB; recomendado: {required_gb:.2f} GB."
    )


def _restore_firebird_backup(
    job: Job,
    gbak: str,
    auth: list[str],
    backup_file: Path,
    restored_working: Path,
    paths: dict[str, Path],
    env: dict[str, str],
) -> None:
    restored_working.unlink(missing_ok=True)
    restore = _run_command(
        job,
        paths["log"],
        [gbak, *auth, "-c", "-v", str(backup_file), str(restored_working)],
        paths["processing"],
        check=False,
        env=env,
    )
    if restore.returncode == 0 and restored_working.exists():
        return
    _raise_no_space_error(restore, restored_working)

    restored_working.unlink(missing_ok=True)
    _append_log(job, paths["log"], "Restore com autenticacao falhou; tentando restore local sem usuario/senha.")
    fallback = _run_command(
        job,
        paths["log"],
        [gbak, "-c", "-v", str(backup_file), str(restored_working)],
        paths["processing"],
        check=False,
        env=env,
    )
    if fallback.returncode == 0 and restored_working.exists():
        return
    _raise_no_space_error(fallback, restored_working)

    code = restore.returncode if restore.returncode != 0 else fallback.returncode
    if code < 0:
        signal_number = abs(code)
        raise RunnerError(
            f"gbak encerrou por sinal {signal_number} durante o restore. "
            "Verifique as bibliotecas/dependencias do Firebird 2.5 no servidor."
        )
    raise RunnerError(f"Comando falhou com codigo {code}.")


def run_job(job: Job, module: ModuleDefinition, uploaded_file: Path) -> None:
    paths = job_dirs(job.id)
    log_path = paths["log"]
    job.status = "processing"
    _append_log(job, log_path, "Job iniciado.")
    try:
        if module.runner == "analise_xml_nfe":
            _run_analise_xml(job, uploaded_file, paths)
        elif module.runner == "analise_xml_nfce":
            _run_analise_nfce(job, uploaded_file, paths)
        elif module.runner == "analise_log_nfse":
            _run_analise_log(job, uploaded_file, paths)
        elif module.runner == "autoexec_automation":
            _run_autoexec(job, uploaded_file, paths)
        elif module.runner == "firebird_repair":
            _run_firebird_repair(job, uploaded_file, paths)
        elif module.runner == "logo_adjustment":
            _run_logo_adjustment(job, uploaded_file, paths)
        else:
            raise RunnerError("Modulo sem runner executavel.")
        job.status = "completed"
        if not job.result:
            job.result = "Processamento concluido."
        _append_log(job, log_path, "Job concluido.")
        _cleanup_transient_paths(paths)
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        if log_path.exists():
            job.report_path = log_path
        _append_log(job, log_path, f"ERRO: {exc}")
        _cleanup_transient_paths(paths, include_result=True)
    finally:
        shutil.rmtree(paths["upload"], ignore_errors=True)


def _run_analise_xml(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    processing_input, is_batch, count = _prepare_xml_input(uploaded_file, paths)
    _run_command(job, paths["log"], [str(_binary("analise_xml_nfe")), str(processing_input)], paths["processing"])
    report = paths["processing"] / "SAIDA" / "relatorio.txt"
    if not report.exists():
        raise RunnerError("Relatorio nao foi gerado pelo modulo.")
    report_txt = copy_to(report, paths["result"] / "relatorio.txt")
    job.report_path = _text_report_to_pdf(report_txt, "Relatorio XML NF-e", paths["result"] / "relatorio.pdf")
    report_txt.unlink(missing_ok=True)
    if is_batch:
        job.result = f"Relatorio XML NF-e gerado para {count} arquivo(s)."
    else:
        job.result = "Relatorio XML NF-e gerado."


def _run_analise_nfce(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    processing_input, is_batch, count = _prepare_xml_input(uploaded_file, paths)
    binary = _binary("analise_xml_nfce")

    if is_batch:
        xml_files = sorted(processing_input.glob("*.xml"))
        if not xml_files:
            raise RunnerError("Nenhum arquivo XML encontrado no lote.")

        consolidated_lines: list[str] = []
        for index, xml_file in enumerate(xml_files, 1):
            completed = _run_command(
                job,
                paths["log"],
                [str(binary), str(xml_file)],
                paths["processing"],
                check=False
            )
            if completed.returncode == 0:
                consolidated_lines.append(completed.stdout)
            else:
                consolidated_lines.append(f"ERRO ao processar arquivo {xml_file.name}: {completed.stdout or 'Erro desconhecido'}")

            if index < len(xml_files):
                consolidated_lines.append("\n" + "=" * 90 + "\n\n")

        report_content = "".join(consolidated_lines)
        report_txt = paths["result"] / "relatorio.txt"
        report_txt.write_text(report_content, encoding="utf-8")
        job.report_path = _text_report_to_pdf(report_txt, "Relatorio XML NFC-e (Lote)", paths["result"] / "relatorio.pdf")
        report_txt.unlink(missing_ok=True)
        job.result = f"Relatorio XML NFC-e gerado para {len(xml_files)} arquivo(s)."

    else:
        completed = _run_command(
            job,
            paths["log"],
            [str(binary), str(processing_input)],
            paths["processing"]
        )
        report_txt = paths["result"] / "relatorio.txt"
        report_txt.write_text(completed.stdout, encoding="utf-8")
        job.report_path = _text_report_to_pdf(report_txt, "Relatorio XML NFC-e", paths["result"] / "relatorio.pdf")
        report_txt.unlink(missing_ok=True)
        job.result = "Relatorio XML NFC-e gerado."


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
        job.output_path = _zip_files(package, outputs, output_dir)
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
    job.output_path = _zip_files(paths["result"] / "autoexec_resultado.zip", [output_sql])
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
    _configure_firebird_runtime(firebird_env, paths)
    _append_log(job, paths["log"], f"FIREBIRD={firebird_env['FIREBIRD']}")

    repair_input = _prepare_fdb_input(uploaded_file, paths)
    working = copy_to(repair_input, paths["processing"] / "working.fdb")
    if repair_input != uploaded_file and paths["processing"].resolve() in repair_input.resolve().parents:
        shutil.rmtree(repair_input.parent, ignore_errors=True)
    uploaded_file.unlink(missing_ok=True)
    backup_file = paths["processing"] / "repair.fbk"
    repaired = paths["result"] / "repaired.fdb"

    _append_log(job, paths["log"], f"Copia de trabalho isolada criada: {working.name}")
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
    _ensure_restore_space(working, repaired)
    _restore_firebird_backup(job, gbak, auth, backup_file, repaired, paths, firebird_env)

    if not repaired.exists():
        raise RunnerError("Base reparada nao foi gerada.")
    package = _zip_files(paths["result"] / "reparo_firebird_resultado.zip", [repaired])
    repaired.unlink(missing_ok=True)
    job.output_path = package
    job.report_path = paths["log"]
    job.result = "Base Firebird reparada em copia isolada."


def _save_resized_logo(source: Image.Image, output_path: Path, width: int, height: int) -> Path:
    canvas = Image.new("RGB", (width, height), "white")
    working = source.copy()
    working.thumbnail((width, height), Image.Resampling.LANCZOS)
    x = (width - working.width) // 2
    y = (height - working.height) // 2

    rgba = working.convert("RGBA")
    canvas.paste(rgba, (x, y), rgba.getchannel("A"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        canvas.save(output_path, "JPEG", quality=95)
    else:
        canvas.save(output_path, "BMP")
    return output_path


def _run_logo_adjustment(job: Job, uploaded_file: Path, paths: dict[str, Path]) -> None:
    outputs = [
        ("logofrente.bmp", 350, 350),
        ("logonfse.jpg", 100, 100),
        ("logonfe.bmp", 530, 340),
        ("logopaf.bmp", 332, 278),
        ("LOGOTIP.BMP", 360, 90),
        ("logotip.jpg", 360, 90),
    ]

    try:
        with Image.open(uploaded_file) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            image.load()
    except UnidentifiedImageError as exc:
        raise RunnerError("Imagem invalida ou formato nao suportado.") from exc

    output_dir = paths["processing"] / "SAIDA"
    generated = [
        _save_resized_logo(image, output_dir / filename, width, height)
        for filename, width, height in outputs
    ]
    job.output_path = _zip_files(paths["result"] / "logos_ajustadas.zip", generated, output_dir)
    job.result = "Logos ajustadas e compactadas. O arquivo ficara disponivel por 5 minutos."
