from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import os
import sys

try:
    from firebird.driver import connect, driver_config
except ImportError as exc:
    raise SystemExit(
        "Dependencia ausente: instale com `python -m pip install -r requirements.txt`."
    ) from exc


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


def load_columns(database: str, user: str, password: str, charset: str) -> dict[str, list[dict]]:
    query = """
        SELECT
            TRIM(rf.RDB$RELATION_NAME) AS table_name,
            TRIM(rf.RDB$FIELD_NAME) AS column_name,
            rf.RDB$FIELD_POSITION AS position,
            f.RDB$FIELD_TYPE AS field_type,
            f.RDB$FIELD_LENGTH AS field_length,
            f.RDB$FIELD_SCALE AS field_scale,
            f.RDB$FIELD_PRECISION AS field_precision,
            f.RDB$FIELD_SUB_TYPE AS field_subtype,
            rf.RDB$NULL_FLAG AS null_flag,
            TRIM(COALESCE(rf.RDB$DESCRIPTION, '')) AS description
        FROM RDB$RELATION_FIELDS rf
        JOIN RDB$FIELDS f
          ON f.RDB$FIELD_NAME = rf.RDB$FIELD_SOURCE
        JOIN RDB$RELATIONS r
          ON r.RDB$RELATION_NAME = rf.RDB$RELATION_NAME
        WHERE COALESCE(r.RDB$SYSTEM_FLAG, 0) = 0
          AND r.RDB$VIEW_BLR IS NULL
        ORDER BY rf.RDB$RELATION_NAME, rf.RDB$FIELD_POSITION
    """
    tables: dict[str, list[dict]] = {}
    try:
        with connect(database=database, user=user, password=password, charset=charset) as con:
            cur = con.cursor()
            cur.execute(query)
            for row in cur:
                table_name = row[0]
                tables.setdefault(table_name, []).append(
                    {
                        "name": row[1],
                        "type": format_type(row[3], row[4], row[5], row[6], row[7]),
                        "nullable": not bool(row[8]),
                        "description": row[9] or "",
                    }
                )
    except Exception as exc:
        if "Firebird Client Library" in str(exc):
            raise SystemExit(
                "Biblioteca cliente do Firebird nao encontrada. Informe com "
                "`--client-library /caminho/libfbclient.so` ou defina FIREBIRD_CLIENT_LIBRARY."
            ) from exc
        raise
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
    parser.add_argument("--client-library", default=os.getenv("FIREBIRD_CLIENT_LIBRARY", ""))
    args = parser.parse_args()

    if args.client_library:
        driver_config.fb_client_library.value = args.client_library

    tables = load_columns(args.database, args.user, args.password, args.charset)
    if not tables:
        raise SystemExit("Nenhuma tabela de usuario encontrada.")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_yaml(tables), encoding="utf-8")
    print(f"Catalogo gerado em {output} com {len(tables)} tabela(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
