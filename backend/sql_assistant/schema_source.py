from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time

from backend.config import get_settings


TYPE_NAMES = {
    7: "SMALLINT",
    8: "INTEGER",
    10: "FLOAT",
    12: "DATE",
    13: "TIME",
    14: "CHAR",
    16: "BIGINT",
    23: "BOOLEAN",
    27: "DOUBLE PRECISION",
    35: "TIMESTAMP",
    37: "VARCHAR",
    40: "CSTRING",
    261: "BLOB",
}


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type_name: str
    nullable: bool


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: dict[str, list[ColumnInfo]]
    generators: list[str]


_CACHE: tuple[float, SchemaSnapshot] | None = None


def schema_db_enabled() -> bool:
    return bool(get_settings().sql_assistant_schema_db)


def format_type(field_type: int, length: int, scale: int, precision: int, subtype: int) -> str:
    if field_type == 16 and scale < 0:
        return f"NUMERIC({precision or 18},{abs(scale)})"
    if field_type in {14, 37} and length:
        return f"{TYPE_NAMES.get(field_type, 'UNKNOWN')}({length})"
    if field_type == 261 and subtype == 1:
        return "BLOB SUB_TYPE TEXT"
    return TYPE_NAMES.get(field_type, f"UNKNOWN({field_type})")


def metadata_query() -> str:
    return """
        SET LIST ON;
        SET HEADING OFF;

        SELECT
            TRIM(rf.RDB$RELATION_NAME) AS TABLE_NAME,
            TRIM(rf.RDB$FIELD_NAME) AS COLUMN_NAME,
            rf.RDB$FIELD_POSITION AS FIELD_POSITION,
            f.RDB$FIELD_TYPE AS FIELD_TYPE,
            COALESCE(f.RDB$FIELD_LENGTH, 0) AS FIELD_LENGTH,
            COALESCE(f.RDB$FIELD_SCALE, 0) AS FIELD_SCALE,
            COALESCE(f.RDB$FIELD_PRECISION, 0) AS FIELD_PRECISION,
            COALESCE(f.RDB$FIELD_SUB_TYPE, 0) AS FIELD_SUBTYPE,
            COALESCE(rf.RDB$NULL_FLAG, 0) AS NULL_FLAG
        FROM RDB$RELATION_FIELDS rf
        JOIN RDB$FIELDS f
          ON f.RDB$FIELD_NAME = rf.RDB$FIELD_SOURCE
        JOIN RDB$RELATIONS r
          ON r.RDB$RELATION_NAME = rf.RDB$RELATION_NAME
        WHERE COALESCE(r.RDB$SYSTEM_FLAG, 0) = 0
          AND r.RDB$VIEW_BLR IS NULL
        ORDER BY rf.RDB$RELATION_NAME, rf.RDB$FIELD_POSITION;

        SELECT
            TRIM(RDB$GENERATOR_NAME) AS GENERATOR_NAME
        FROM RDB$GENERATORS
        WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0
        ORDER BY RDB$GENERATOR_NAME;
    """


def _parse_list_records(output: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    record: dict[str, str] = {}
    valid_keys = {
        "TABLE_NAME",
        "COLUMN_NAME",
        "FIELD_POSITION",
        "FIELD_TYPE",
        "FIELD_LENGTH",
        "FIELD_SCALE",
        "FIELD_PRECISION",
        "FIELD_SUBTYPE",
        "NULL_FLAG",
        "GENERATOR_NAME",
    }

    def flush() -> None:
        nonlocal record
        if record:
            records.append(record)
            record = {}

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            flush()
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        if key in valid_keys:
            record[key] = value.strip()
    flush()
    return records


def _parse_snapshot(output: str) -> SchemaSnapshot:
    tables: dict[str, list[ColumnInfo]] = {}
    generators: list[str] = []
    for record in _parse_list_records(output):
        if record.get("TABLE_NAME") and record.get("COLUMN_NAME"):
            table_name = record["TABLE_NAME"]
            tables.setdefault(table_name, []).append(
                ColumnInfo(
                    name=record["COLUMN_NAME"],
                    type_name=format_type(
                        int(record.get("FIELD_TYPE", "0")),
                        int(record.get("FIELD_LENGTH", "0")),
                        int(record.get("FIELD_SCALE", "0")),
                        int(record.get("FIELD_PRECISION", "0")),
                        int(record.get("FIELD_SUBTYPE", "0")),
                    ),
                    nullable=record.get("NULL_FLAG", "0") != "1",
                )
            )
        elif record.get("GENERATOR_NAME"):
            generators.append(record["GENERATOR_NAME"])
    return SchemaSnapshot(tables=tables, generators=generators)


def _resolve_schema_db() -> str:
    settings = get_settings()
    database = settings.sql_assistant_schema_db
    path = Path(database)
    if not path.is_absolute() and ("/" in database or "\\" in database):
        return str(settings.root_dir / path)
    return database


def _isql_environment() -> dict[str, str]:
    settings = get_settings()
    env = os.environ.copy()
    if settings.sql_assistant_firebird_home:
        env["FIREBIRD"] = settings.sql_assistant_firebird_home
    if settings.sql_assistant_firebird_lib:
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = settings.sql_assistant_firebird_lib if not current else f"{settings.sql_assistant_firebird_lib}:{current}"
    return env


def _run_isql_metadata() -> str:
    settings = get_settings()
    resolved_isql = (
        settings.sql_assistant_isql_bin
        if Path(settings.sql_assistant_isql_bin).exists()
        else shutil.which(settings.sql_assistant_isql_bin)
    )
    if not resolved_isql:
        raise RuntimeError("isql nao encontrado para consultar metadados do Assistente SQL.")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as script:
        script.write(metadata_query())
        script_path = script.name
    try:
        completed = subprocess.run(
            [
                str(resolved_isql),
                _resolve_schema_db(),
                "-user",
                settings.firebird_user,
                "-password",
                settings.firebird_password,
                "-ch",
                "UTF8",
                "-i",
                script_path,
            ],
            env=_isql_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)

    if completed.returncode != 0:
        raise RuntimeError(f"isql falhou ao consultar metadados: {completed.stdout[:1000]}")
    if "Statement failed" in completed.stdout or "SQL error" in completed.stdout:
        raise RuntimeError(f"isql retornou erro ao consultar metadados: {completed.stdout[:1000]}")
    return completed.stdout


def load_schema_snapshot() -> SchemaSnapshot:
    global _CACHE
    settings = get_settings()
    now = time.monotonic()
    if _CACHE and now - _CACHE[0] < settings.sql_assistant_schema_cache_seconds:
        return _CACHE[1]

    snapshot = _parse_snapshot(_run_isql_metadata())
    if not snapshot.tables:
        raise RuntimeError("Nenhuma tabela de usuario encontrada na base de metadados do Assistente SQL.")
    _CACHE = (now, snapshot)
    return snapshot


def table_blocks_from_snapshot(snapshot: SchemaSnapshot) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for table_name in sorted(snapshot.tables):
        lines = [f"  {table_name}:", "    columns:"]
        for column in snapshot.tables[table_name]:
            nullable = str(column.nullable).lower()
            lines.append(f"      {column.name}: {{type: \"{column.type_name}\", nullable: {nullable}}}")
        blocks.append((table_name, "\n".join(lines)))
    return blocks


def generators_index_from_snapshot(snapshot: SchemaSnapshot) -> str:
    if not snapshot.generators:
        return "Nenhum generator de usuario encontrado."
    return ", ".join(snapshot.generators)
