from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile


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


def yaml_quote(value: str) -> str:
    safe = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'


def format_type(field_type: int, length: int | None, scale: int | None, precision: int | None, subtype: int | None) -> str:
    if field_type == 16 and scale and scale < 0:
        numeric_precision = precision or 18
        return f"NUMERIC({numeric_precision},{abs(scale)})"
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
    """


def parse_isql_list_output(output: str) -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = {}
    record: dict[str, str] = {}

    def flush() -> None:
        if not record.get("TABLE_NAME") or not record.get("COLUMN_NAME"):
            return
        table_name = record["TABLE_NAME"]
        tables.setdefault(table_name, []).append(
            {
                "name": record["COLUMN_NAME"],
                "type": format_type(
                    int(record.get("FIELD_TYPE", "0")),
                    int(record.get("FIELD_LENGTH", "0")),
                    int(record.get("FIELD_SCALE", "0")),
                    int(record.get("FIELD_PRECISION", "0")),
                    int(record.get("FIELD_SUBTYPE", "0")),
                ),
                "nullable": record.get("NULL_FLAG", "0") != "1",
                "description": "",
            }
        )

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            flush()
            record = {}
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        if key in {
            "TABLE_NAME",
            "COLUMN_NAME",
            "FIELD_POSITION",
            "FIELD_TYPE",
            "FIELD_LENGTH",
            "FIELD_SCALE",
            "FIELD_PRECISION",
            "FIELD_SUBTYPE",
            "NULL_FLAG",
        }:
            record[key] = value.strip()
    flush()
    return tables


def load_columns(database: str, user: str, password: str, charset: str, isql_bin: str) -> dict[str, list[dict]]:
    resolved_isql = shutil.which(isql_bin) if not Path(isql_bin).exists() else isql_bin
    if not resolved_isql:
        raise SystemExit("isql nao encontrado. Informe --isql-bin com o caminho completo do Firebird 2.5.")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as script:
        script.write(metadata_query())
        script_path = script.name
    try:
        completed = subprocess.run(
            [resolved_isql, database, "-user", user, "-password", password, "-ch", charset, "-i", script_path],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)

    if completed.returncode != 0:
        raise SystemExit(f"isql falhou com codigo {completed.returncode}:\n{completed.stdout}")
    if "Statement failed" in completed.stdout or "SQL error" in completed.stdout:
        raise SystemExit(f"isql retornou erro:\n{completed.stdout}")
    tables = parse_isql_list_output(completed.stdout)
    return tables


def render_yaml(tables: dict[str, list[dict]]) -> str:
    lines = [
        "# Catalogo controlado do Small Commerce para o Assistente SQL.",
        "# Gerado automaticamente a partir de metadados Firebird; nao contem dados de clientes.",
        "",
        "database:",
        "  engine: Firebird",
        "  product: Small Commerce",
        "",
        "rules:",
        "  - Gere SQL apenas para o banco Firebird do Small Commerce.",
        "  - Use somente tabelas e colunas presentes neste catalogo.",
        "  - Quando faltar informacao, explique a premissa ou peca mais contexto.",
        "  - Nao gere comandos DROP, ALTER, CREATE DATABASE, EXECUTE BLOCK, GRANT ou REVOKE.",
        "  - UPDATE, INSERT e DELETE sao permitidos somente como scripts para revisao humana.",
        "  - Nao use WHERE 1=0 como protecao; isso invalida scripts que devem aplicar alteracao em massa.",
        "  - UPDATE sem WHERE e valido quando a solicitacao pedir todos os registros, mas deve trazer aviso de risco.",
        "",
        "tables:",
    ]
    for table_name in sorted(tables):
        lines.append(f"  {table_name}:")
        lines.append("    columns:")
        for column in tables[table_name]:
            details = [f"type: {yaml_quote(column['type'])}", f"nullable: {str(column['nullable']).lower()}"]
            if column["description"]:
                details.append(f"description: {yaml_quote(column['description'])}")
            lines.append(f"      {column['name']}: {{{', '.join(details)}}}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = ArgumentParser(description="Exporta metadados de um .fdb Firebird para o catalogo YAML do Assistente SQL.")
    parser.add_argument("database", help="Caminho do arquivo .fdb ou string de conexao Firebird.")
    parser.add_argument("--user", default="SYSDBA")
    parser.add_argument("--password", default="masterkey")
    parser.add_argument("--charset", default="UTF8")
    parser.add_argument("--output", default="backend/sql_assistant/small_commerce_schema.yaml")
    parser.add_argument("--isql-bin", default=os.getenv("ISQL_BIN", "isql"))
    args = parser.parse_args()

    tables = load_columns(args.database, args.user, args.password, args.charset, args.isql_bin)
    if not tables:
        raise SystemExit("Nenhuma tabela de usuario encontrada.")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_yaml(tables), encoding="utf-8")
    print(f"Catalogo gerado em {output} com {len(tables)} tabela(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
