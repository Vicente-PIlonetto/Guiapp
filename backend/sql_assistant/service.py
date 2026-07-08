from functools import lru_cache
from pathlib import Path
import json
import re
import urllib.error
import urllib.request

from backend.config import get_settings


DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(DROP|ALTER|TRUNCATE|CREATE\s+DATABASE|EXECUTE\s+BLOCK|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
MODIFICATION_SQL_RE = re.compile(r"\b(UPDATE|INSERT|DELETE)\b", re.IGNORECASE)
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
UPDATE_WHERE_TRUE_RE = re.compile(r"^(\s*UPDATE\b.+?)\s+WHERE\s+1\s*=\s*1\s*;?\s*$", re.IGNORECASE | re.DOTALL)
SQL_START_RE = re.compile(r"^\s*(SELECT|UPDATE|INSERT|DELETE)\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Z0-9_]{3,}", re.IGNORECASE)


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


def relevant_catalog(question: str, max_blocks: int = 8) -> str:
    catalog = load_small_commerce_catalog()
    rules = catalog.split("\ntables:\n", 1)[0].strip()
    question_tokens = {token.upper() for token in TOKEN_RE.findall(question)}
    scored: list[tuple[int, str, str]] = []
    for table_name, block in catalog_table_blocks():
        block_upper = block.upper()
        score = 0
        if table_name in question_tokens:
            score += 8
        for token in question_tokens:
            if token in block_upper:
                score += 1
        if score:
            scored.append((score, table_name, block))

    if not scored:
        aliases = {
            "ESTOQUE": ("ESTOQUE", "PRODUTO", "PRODUTOS", "ITEM", "ITENS"),
        }
        for table_name, terms in aliases.items():
            if any(term in question_tokens for term in terms):
                for candidate_name, block in catalog_table_blocks():
                    if candidate_name == table_name:
                        scored.append((10, candidate_name, block))

    selected = [block for _, _, block in sorted(scored, reverse=True)[:max_blocks]]
    if not selected:
        selected = [block for _, block in catalog_table_blocks()[:3]]
    return f"{rules}\n\ntables:\n" + "\n".join(selected)


def _refusal(message: str) -> dict:
    return {
        "sql": "",
        "explanation": message,
        "warnings": ["Solicitacao recusada por regra de seguranca."],
    }


def _system_prompt(question: str) -> str:
    return (
        "Voce e um assistente interno de suporte para gerar SQL Firebird do Small Commerce.\n"
        "Nunca execute SQL. Gere apenas texto para revisao humana.\n"
        "Use somente tabelas, colunas, relacionamentos e regras presentes no catalogo abaixo.\n"
        "Se a pergunta exigir informacao ausente no catalogo, nao invente: explique o que falta.\n"
        "Voce pode gerar SELECT, UPDATE, INSERT e DELETE. Para UPDATE, INSERT e DELETE, inclua aviso forte.\n"
        "Nao adicione WHERE 1=0, filtros falsos ou placeholders que mudem o efeito pedido pelo usuario.\n"
        "Nao adicione WHERE 1=1 quando a intencao for todos os registros; deixe o UPDATE sem WHERE.\n"
        "UPDATE sem WHERE e valido quando o usuario pedir alteracao em massa/todos os registros; apenas avise o risco.\n"
        "Se uma condicao for necessaria mas nao foi informada, pergunte pela condicao em vez de criar uma condicao falsa.\n"
        "Recuse DROP, ALTER, TRUNCATE, CREATE DATABASE, EXECUTE BLOCK, GRANT, REVOKE e qualquer comando fora do escopo.\n"
        "Retorne apenas um comando SQL, sem exemplos alternativos.\n"
        "Nao retorne texto fora do JSON. Nao use markdown. Nao use bloco ```sql.\n"
        "O campo sql deve conter somente o comando SQL final, sem comentarios, sem explicacao e sem multiplos comandos.\n"
        "O SQL deve estar em uma unica linha, sem quebras de linha, para uso direto no Small Commerce.\n"
        "A explicacao deve ter no maximo duas frases curtas.\n"
        "Responda exclusivamente como JSON valido no formato:\n"
        '{"sql":"...","explanation":"...","warnings":["..."]}\n\n'
        f"CATALOGO SMALL COMMERCE RELEVANTE:\n{relevant_catalog(question)}"
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


def _extract_json_content(content: str) -> dict:
    candidate = content.strip()
    match = JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(1)
    elif "{" in candidate and "}" in candidate:
        candidate = candidate[candidate.find("{"):candidate.rfind("}") + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        raise SqlAssistantError("A IA retornou texto fora do formato JSON esperado. Tente reformular a solicitacao.")

    raw_warnings = parsed.get("warnings") or []
    if isinstance(raw_warnings, str):
        warnings = [raw_warnings]
    elif isinstance(raw_warnings, list):
        warnings = [str(item) for item in raw_warnings]
    else:
        warnings = []

    return {
        "sql": _single_line_sql(str(parsed.get("sql") or "")),
        "explanation": str(parsed.get("explanation") or "").strip(),
        "warnings": warnings,
    }


def _call_hermes(question: str) -> str:
    settings = get_settings()
    if not settings.hermes_api_url:
        raise SqlAssistantError("HERMES_API_URL nao configurado no servidor.")

    payload = {
        "model": settings.hermes_model or None,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": _system_prompt(question)},
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

    result = _extract_json_content(_call_hermes(cleaned_question))
    sql = result["sql"]
    if not sql or not SQL_START_RE.search(sql):
        raise SqlAssistantError("A IA nao retornou um comando SQL valido.")
    if _has_multiple_sql_commands(sql):
        raise SqlAssistantError("A IA retornou multiplos comandos. Refine a solicitacao para gerar apenas um comando.")
    if DESTRUCTIVE_SQL_RE.search(sql):
        return _refusal("A resposta foi bloqueada porque continha comando destrutivo ou administrativo.")

    warnings = list(dict.fromkeys(result["warnings"]))
    if MODIFICATION_SQL_RE.search(sql):
        warnings.append("Script de alteracao: revise, teste em base de homologacao e faca backup antes de usar.")

    return {
        "sql": sql,
        "explanation": result["explanation"],
        "warnings": warnings,
    }
