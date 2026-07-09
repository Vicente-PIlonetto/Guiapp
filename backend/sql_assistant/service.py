from functools import lru_cache
from pathlib import Path
import http.client
import json
import re
import urllib.error
import urllib.request

from backend.config import get_settings
from backend.sql_assistant import schema_source


DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(DROP|ALTER|TRUNCATE|CREATE\s+DATABASE|EXECUTE\s+BLOCK|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
MODIFICATION_SQL_RE = re.compile(r"\b(UPDATE|INSERT|DELETE)\b", re.IGNORECASE)
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
SQL_BLOCK_RE = re.compile(r"```(?:sql)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
SQL_COMMAND_RE = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE|SET\s+GENERATOR)\b[\s\S]*?(?:;|$)", re.IGNORECASE)
UPDATE_WHERE_TRUE_RE = re.compile(r"^(\s*UPDATE\b.+?)\s+WHERE\s+1\s*=\s*1\s*;?\s*$", re.IGNORECASE | re.DOTALL)
SQL_START_RE = re.compile(r"^\s*(SELECT|UPDATE|INSERT|DELETE|SET\s+GENERATOR)\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Z0-9_]{3,}", re.IGNORECASE)
SQL_IDENTIFIER_RE = re.compile(r"\b[A-Z_][A-Z0-9_]*\b", re.IGNORECASE)
ESTOQUE_TERMS = {"ESTOQUE", "PRODUTO", "PRODUTOS", "ITEM", "ITENS"}


class SqlAssistantError(RuntimeError):
    pass


@lru_cache
def load_small_commerce_catalog() -> str:
    path = Path(__file__).resolve().parent / "small_commerce_schema.yaml"
    return path.read_text(encoding="utf-8")


@lru_cache
def catalog_table_blocks() -> list[tuple[str, str]]:
    catalog = load_small_commerce_catalog()
    marker = "\ntables:\n"
    if marker not in catalog:
        return []
    blocks: list[tuple[str, str]] = []
    current_name = ""
    current_lines: list[str] = []
    for line in catalog.splitlines()[catalog.splitlines().index("tables:") + 1:]:
        table_match = re.match(r"^  ([A-Z0-9_]+):\s*$", line)
        if table_match:
            if current_name:
                blocks.append((current_name, "\n".join(current_lines)))
            current_name = table_match.group(1)
            current_lines = [line]
            continue
        if current_name:
            current_lines.append(line)
    if current_name:
        blocks.append((current_name, "\n".join(current_lines)))
    return blocks


def active_table_blocks() -> list[tuple[str, str]]:
    if schema_source.schema_db_enabled():
        snapshot = schema_source.load_schema_snapshot()
        return schema_source.table_blocks_from_snapshot(snapshot)
    return catalog_table_blocks()


def active_generators_index() -> str:
    if schema_source.schema_db_enabled():
        snapshot = schema_source.load_schema_snapshot()
        return schema_source.generators_index_from_snapshot(snapshot)
    catalog = load_small_commerce_catalog()
    if "\ngenerators:\n" not in catalog:
        return "Generators nao listados no catalogo YAML."
    after = catalog.split("\ngenerators:\n", 1)[1]
    before_tables = after.split("\ntables:\n", 1)[0].strip()
    return before_tables or "Generators nao listados no catalogo YAML."


def _truncate_text(value: str, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...conteudo reduzido para acelerar a resposta..."


def relevant_catalog(question: str, max_blocks: int = 8) -> str:
    catalog = load_small_commerce_catalog()
    rules = catalog.split("\ntables:\n", 1)[0].strip()
    question_tokens = {token.upper() for token in TOKEN_RE.findall(question)}
    scored: list[tuple[int, str, str]] = []
    for table_name, block in active_table_blocks():
        block_upper = block.upper()
        score = 0
        if table_name in question_tokens:
            score += 8
        for token in question_tokens:
            if token in block_upper:
                score += 1
        if score:
            scored.append((score, table_name, block))

    aliases = {
        "ESTOQUE": ("ESTOQUE", "PRODUTO", "PRODUTOS", "ITEM", "ITENS"),
        "VENDAS": ("VENDA", "VENDAS", "NOTA", "NF"),
        "COMPRAS": ("COMPRA", "COMPRAS", "NOTA", "NF"),
        "NFCE": ("NFCE", "NFC", "NFC_E", "CUPOM"),
    }
    for table_name, terms in aliases.items():
        if any(term in question_tokens for term in terms):
            for candidate_name, block in active_table_blocks():
                if candidate_name == table_name and all(candidate_name != item[1] for item in scored):
                    scored.append((10, candidate_name, block))

    selected = [block for _, _, block in sorted(scored, reverse=True)[:max_blocks]]
    if not selected:
        selected = [block for _, block in active_table_blocks()[:3]]
    return _truncate_text(f"{rules}\n\ntables:\n" + "\n".join(selected), 12000)


def _table_columns(table_block: str) -> list[str]:
    return re.findall(r"^      ([A-Z0-9_]+):", table_block, re.MULTILINE)


def compact_table_index() -> str:
    table_names = [table_name for table_name, _ in active_table_blocks()]
    return _truncate_text(", ".join(sorted(table_names)), 4000)


def _columns_for_table(table_name: str) -> set[str]:
    for candidate, block in active_table_blocks():
        if candidate == table_name:
            return set(_table_columns(block))
    return set()


def priority_context(question: str) -> str:
    question_tokens = {token.upper() for token in TOKEN_RE.findall(question)}
    lines: list[str] = []
    if question_tokens & ESTOQUE_TERMS:
        columns = _columns_for_table("ESTOQUE")
        if columns:
            ordered_columns = sorted(columns)
            lines.append("Tabela prioritaria para estoque/produtos/itens: ESTOQUE.")
            lines.append(f"Todas as colunas disponiveis em ESTOQUE: {', '.join(ordered_columns)}.")
            if "CST_ICMS" in question.upper() and "CST_ICMS" not in columns and "CST" in columns:
                lines.append("Campo solicitado CST_ICMS: use a coluna real CST da tabela ESTOQUE.")
            if re.search(r"CSOSN[\s_]*NFCE|CSOSN[\s_]*NFC[\s_-]*E", question, re.IGNORECASE) and "CSOSN_NFCE" in columns:
                lines.append("Campo solicitado CSOSN NFCE: use a coluna real CSOSN_NFCE da tabela ESTOQUE.")
            lines.append("Nao use TB_ESTOQUE, TBL_ESTOQUE ou CAD_ESTOQUE se esses nomes nao aparecerem no catalogo.")

    if {"VENDAS", "VENDA", "COMPRAS", "COMPRA", "NOTA"} & question_tokens:
        lines.append("Para localizar nota em VENDAS/COMPRAS, a serie deve ser embutida no valor de NUMERONF; nao use coluna SERIE.")

    return "\n".join(lines) if lines else "Sem contexto prioritario inferido; use somente o catalogo abaixo."


def _refusal(message: str) -> dict:
    return {
        "sql": "",
        "explanation": message,
        "warnings": ["Solicitacao recusada por regra de seguranca."],
    }


def _system_prompt(question: str, validation_feedback: str = "") -> str:
    correction = ""
    if validation_feedback:
        correction = (
            "A tentativa anterior foi rejeitada pela validacao contra o catalogo de metadados.\n"
            f"Erro encontrado: {validation_feedback}\n"
            "Corrija usando somente tabelas e colunas listadas no catalogo. Nao repita tabela ou coluna invalida.\n"
            "Se o erro informar um nome correto, substitua obrigatoriamente pelo nome correto e retorne apenas o SQL corrigido.\n\n"
        )
    return (
        "Voce e um assistente interno de suporte para gerar SQL Firebird do Small Commerce.\n"
        "Nunca execute SQL. Gere apenas texto para revisao humana.\n"
        "Use somente tabelas, colunas, relacionamentos e regras presentes no catalogo abaixo.\n"
        "Use os nomes EXATOS do catalogo. Nao adicione prefixos como TB_, TBL_ ou CAD_ se eles nao existirem no catalogo.\n"
        "Se a pergunta exigir informacao ausente no catalogo, nao invente: explique o que falta.\n"
        "Voce pode gerar SELECT, UPDATE, INSERT e DELETE. Para UPDATE, INSERT e DELETE, inclua aviso forte.\n"
        "Nao adicione WHERE 1=0, filtros falsos ou placeholders que mudem o efeito pedido pelo usuario.\n"
        "Nao adicione WHERE 1=1 quando a intencao for todos os registros; deixe o UPDATE sem WHERE.\n"
        "UPDATE sem WHERE e valido quando o usuario pedir alteracao em massa/todos os registros; apenas avise o risco.\n"
        "Se uma condicao for necessaria mas nao foi informada, pergunte pela condicao em vez de criar uma condicao falsa.\n"
        "Recuse DROP, ALTER, TRUNCATE, CREATE DATABASE, EXECUTE BLOCK, GRANT, REVOKE e qualquer comando fora do escopo.\n"
        "Responda somente com um comando SQL final, sem JSON, sem markdown, sem comentarios e sem explicacao.\n"
        "Retorne apenas um comando SQL, sem exemplos alternativos e sem multiplos comandos.\n"
        "O SQL deve estar em uma unica linha, sem quebras de linha, para uso direto no Small Commerce.\n\n"
        "Regras de negocio do Small Commerce que devem ser aplicadas pela IA:\n"
        "- Para produtos/itens/estoque, use a tabela ESTOQUE quando ela existir no catalogo; nao use TB_ESTOQUE se TB_ESTOQUE nao estiver no YAML.\n"
        "- Para localizar nota em VENDAS ou COMPRAS, use a coluna NUMERONF quando ela existir no catalogo.\n"
        "- Em VENDAS/COMPRAS, NUMERONF combina numero da nota com zeros a esquerda e serie com 3 digitos; exemplo nota 123 serie 1: '0000000123001'.\n"
        "- Nao use coluna SERIE em VENDAS ou COMPRAS para localizar notas; a serie deve estar embutida no valor de NUMERONF.\n"
        "- Para NFC-e/cupom, use a tabela e coluna reais do catalogo e formate a numeracao com ate 5 digitos; exemplo cupom 1: '00001'.\n"
        "- Para campos fiscais do ESTOQUE, use a coluna real existente no catalogo. Se o usuario disser CST_ICMS mas o catalogo tiver CST, use CST. Se disser CSOSN NFCE, prefira CSOSN_NFCE quando existir.\n"
        "- Para SET GENERATOR, use o generator do catalogo e coloque o numero anterior ao documento que deve iniciar.\n\n"
        f"{correction}"
        f"CONTEXTO PRIORITARIO PARA ESTA SOLICITACAO:\n{priority_context(question)}\n\n"
        f"INDICE DE GENERATORS:\n{_truncate_text(active_generators_index(), 2000)}\n\n"
        f"INDICE DE TABELAS DISPONIVEIS:\n{compact_table_index()}\n\n"
        f"BLOCOS DETALHADOS MAIS RELEVANTES:\n{relevant_catalog(question)}"
    )


def _single_line_sql(sql: str) -> str:
    cleaned = " ".join(sql.replace(";", " ;").split()).replace(" ;", ";").strip()
    match = UPDATE_WHERE_TRUE_RE.match(cleaned)
    if match:
        return match.group(1).rstrip() + ";"
    return cleaned


def _has_multiple_sql_commands(sql: str) -> bool:
    statements = [item.strip() for item in sql.split(";") if item.strip()]
    return len(statements) > 1


def _catalog_columns_by_table() -> dict[str, set[str]]:
    return {table_name: set(_table_columns(block)) for table_name, block in active_table_blocks()}


def _strip_sql_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def _tables_in_sql(sql: str) -> list[str]:
    tables: list[str] = []
    for pattern in (
        r"\bFROM\s+([A-Z0-9_]+)\b",
        r"\bJOIN\s+([A-Z0-9_]+)\b",
        r"\bUPDATE\s+([A-Z0-9_]+)\b",
        r"\bINTO\s+([A-Z0-9_]+)\b",
        r"\bDELETE\s+FROM\s+([A-Z0-9_]+)\b",
    ):
        for match in re.finditer(pattern, sql, re.IGNORECASE):
            table = match.group(1).upper()
            if table not in tables:
                tables.append(table)
    return tables


def _table_aliases_in_sql(sql: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for pattern in (
        r"\bFROM\s+([A-Z0-9_]+)(?:\s+(?:AS\s+)?([A-Z_][A-Z0-9_]*))?",
        r"\bJOIN\s+([A-Z0-9_]+)(?:\s+(?:AS\s+)?([A-Z_][A-Z0-9_]*))?",
        r"\bUPDATE\s+([A-Z0-9_]+)(?:\s+(?:AS\s+)?([A-Z_][A-Z0-9_]*))?",
        r"\bINTO\s+([A-Z0-9_]+)(?:\s+(?:AS\s+)?([A-Z_][A-Z0-9_]*))?",
        r"\bDELETE\s+FROM\s+([A-Z0-9_]+)(?:\s+(?:AS\s+)?([A-Z_][A-Z0-9_]*))?",
    ):
        for match in re.finditer(pattern, sql, re.IGNORECASE):
            table = match.group(1).upper()
            aliases[table] = table
            alias = (match.group(2) or "").upper()
            if alias and alias not in {"WHERE", "SET", "VALUES", "JOIN", "ORDER", "GROUP"}:
                aliases[alias] = table
    return aliases


def _candidate_columns_in_sql(sql: str) -> set[str]:
    scrubbed = _strip_sql_literals(sql.upper())
    candidates: set[str] = set()
    for pattern in (
        r"\b([A-Z_][A-Z0-9_]*)\s*=",
        r"\b([A-Z_][A-Z0-9_]*)\s+(?:IS|LIKE|IN|BETWEEN|STARTING|CONTAINING)\b",
        r"\bSET\s+(.+?)(?:\s+WHERE\b|;|$)",
        r"\bWHERE\s+(.+?)(?:\s+ORDER\s+BY\b|\s+GROUP\s+BY\b|;|$)",
    ):
        for match in re.finditer(pattern, scrubbed, re.IGNORECASE | re.DOTALL):
            text = match.group(1)
            for token in SQL_IDENTIFIER_RE.findall(text):
                upper_token = token.upper()
                if upper_token not in {
                    "AND", "OR", "NOT", "NULL", "IS", "LIKE", "IN", "BETWEEN", "STARTING", "CONTAINING",
                    "SET", "WHERE", "SELECT", "FROM", "UPDATE", "INSERT", "DELETE", "VALUES",
                }:
                    candidates.add(upper_token)
    return candidates


def _qualified_columns_in_sql(sql: str) -> list[tuple[str, str]]:
    scrubbed = _strip_sql_literals(sql.upper())
    return [(match.group(1), match.group(2)) for match in re.finditer(r"\b([A-Z_][A-Z0-9_]*)\.([A-Z_][A-Z0-9_]*)\b", scrubbed)]


def _validate_sql_against_catalog(sql: str, question: str = "") -> str:
    if re.match(r"^\s*SET\s+GENERATOR\b", sql, re.IGNORECASE):
        return ""

    columns_by_table = _catalog_columns_by_table()
    tables = _tables_in_sql(sql)
    scrubbed_sql = _strip_sql_literals(sql.upper())
    for table in tables:
        if table not in columns_by_table:
            if table.startswith("TB_") and table[3:] in columns_by_table:
                return f"A tabela {table} nao existe no catalogo YAML; use exatamente {table[3:]}."
            return f"A tabela {table} nao existe no catalogo YAML enviado ao assistente."

    if not tables:
        return ""

    question_tokens = {token.upper() for token in TOKEN_RE.findall(question)}
    if question_tokens & ESTOQUE_TERMS and "ESTOQUE" in columns_by_table:
        wrong_tables = [table for table in tables if table != "ESTOQUE"]
        if wrong_tables:
            return f"A solicitacao e sobre estoque/produtos/itens; use a tabela ESTOQUE, nao {', '.join(wrong_tables)}."

    if any(table in {"VENDAS", "COMPRAS"} for table in tables):
        if re.search(r"\bSERIE\b", scrubbed_sql):
            return "Nao use a coluna SERIE em VENDAS/COMPRAS; gere a busca somente por NUMERONF com numero da nota preenchido com zeros a esquerda e serie com 3 digitos no final."
        numeronf_match = re.search(r"\bNUMERONF\s*=\s*'([^']+)'", sql, re.IGNORECASE)
        if numeronf_match and not re.fullmatch(r"\d{13}", numeronf_match.group(1)):
            return "NUMERONF em VENDAS/COMPRAS deve ter 13 digitos: numero da nota com zeros a esquerda seguido da serie com 3 digitos. Exemplo nota 123 serie 1: '0000000123001'."

    aliases = _table_aliases_in_sql(sql)
    for alias, column in _qualified_columns_in_sql(sql):
        table = aliases.get(alias)
        if table and column not in columns_by_table.get(table, set()):
            return f"A coluna {column} nao existe na tabela {table} conforme o catalogo YAML."

    if len(tables) == 1:
        table = tables[0]
        valid_columns = columns_by_table[table]
        for column in sorted(_candidate_columns_in_sql(sql)):
            if column in columns_by_table or column in aliases:
                continue
            if column not in valid_columns:
                return f"A coluna {column} nao existe na tabela {table} conforme o catalogo YAML."

    return ""


def _extract_sql_content(content: str) -> dict:
    candidate = content.strip()
    match = JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(1)
    elif "{" in candidate and "}" in candidate:
        candidate = candidate[candidate.find("{"):candidate.rfind("}") + 1]

    try:
        parsed = json.loads(candidate)
        sql = str(parsed.get("sql") or "")
        raw_warnings = parsed.get("warnings") or []
        if isinstance(raw_warnings, str):
            warnings = [raw_warnings]
        elif isinstance(raw_warnings, list):
            warnings = [str(item) for item in raw_warnings]
        else:
            warnings = []
        return {
            "sql": _single_line_sql(sql),
            "explanation": str(parsed.get("explanation") or "").strip(),
            "warnings": warnings,
        }
    except json.JSONDecodeError:
        pass

    sql_block = SQL_BLOCK_RE.search(content)
    if sql_block:
        sql = sql_block.group(1)
    else:
        sql_match = SQL_COMMAND_RE.search(content)
        if not sql_match:
            raise SqlAssistantError("A IA nao retornou um comando SQL valido.")
        sql = sql_match.group(0)
    return {
        "sql": _single_line_sql(sql),
        "explanation": "",
        "warnings": [],
    }


def _call_hermes(question: str, validation_feedback: str = "") -> str:
    settings = get_settings()
    if not settings.hermes_api_url:
        raise SqlAssistantError("HERMES_API_URL nao configurado no servidor.")

    payload = {
        "model": settings.hermes_model or None,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": _system_prompt(question, validation_feedback)},
            {"role": "user", "content": question},
        ],
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.hermes_api_key:
        headers["Authorization"] = f"Bearer {settings.hermes_api_key}"

    request = urllib.request.Request(settings.hermes_api_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=settings.hermes_timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SqlAssistantError(f"Hermes retornou HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SqlAssistantError(f"Nao foi possivel conectar ao Hermes: {exc.reason}") from exc
    except http.client.RemoteDisconnected as exc:
        raise SqlAssistantError("Ollama/Hermes fechou a conexao sem resposta. O modelo pode estar carregando, sem memoria ou reiniciando; tente novamente em instantes.") from exc
    except TimeoutError as exc:
        raise SqlAssistantError("Tempo limite ao chamar o Hermes.") from exc
    except json.JSONDecodeError as exc:
        raise SqlAssistantError("Hermes retornou resposta invalida.") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SqlAssistantError("Resposta do Hermes nao segue o formato OpenAI-like esperado.") from exc


def generate_sql(question: str) -> dict:
    cleaned_question = question.strip()
    if not cleaned_question:
        raise SqlAssistantError("Informe uma pergunta para gerar o SQL.")
    if len(cleaned_question) > 4000:
        raise SqlAssistantError("Pergunta muito longa. Reduza o texto e tente novamente.")
    if DESTRUCTIVE_SQL_RE.search(cleaned_question):
        return _refusal("Nao posso auxiliar com comandos destrutivos ou administrativos fora do escopo de suporte.")

    result = None
    validation_error = ""
    try:
        for _attempt in range(3):
            result = _extract_sql_content(_call_hermes(cleaned_question, validation_error))
            sql = result["sql"]
            if not sql or not SQL_START_RE.search(sql):
                raise SqlAssistantError("A IA nao retornou um comando SQL valido.")
            if _has_multiple_sql_commands(sql):
                raise SqlAssistantError("A IA retornou multiplos comandos. Refine a solicitacao para gerar apenas um comando.")
            if DESTRUCTIVE_SQL_RE.search(sql):
                return _refusal("A resposta foi bloqueada porque continha comando destrutivo ou administrativo.")
            validation_error = _validate_sql_against_catalog(sql, cleaned_question)
            if not validation_error:
                break
    except RuntimeError as exc:
        raise SqlAssistantError(f"Falha ao consultar metadados da base modelo: {exc}") from exc
    if result is None:
        raise SqlAssistantError("A IA nao retornou um comando SQL valido.")

    sql = result["sql"]
    if validation_error:
        raise SqlAssistantError(validation_error)

    warnings = list(dict.fromkeys(result["warnings"]))
    if MODIFICATION_SQL_RE.search(sql):
        warnings.append("Script de alteracao: revise, teste em base de homologacao e faca backup antes de usar.")

    return {
        "sql": sql,
        "explanation": result["explanation"],
        "warnings": warnings,
    }
