from backend.models import ModuleDefinition

MODULES: dict[str, ModuleDefinition] = {
    "analise-xml-nfe": ModuleDefinition(
        id="analise-xml-nfe",
        name="Analise XML NF-e",
        description="Processa XML NF-e e gera relatorio consolidado de impostos e totais.",
        operation_type="analysis",
        accepted_extensions=(".xml", ".zip", ".rar"),
        requires_confirmation=False,
        generates_report=True,
        generates_output_file=False,
        runner="analise_xml_nfe",
    ),
    "analise-log-nfse": ModuleDefinition(
        id="analise-log-nfse",
        name="Analise Log NFS-e",
        description="Extrai trechos de erro e excecao de logs NFS-e.",
        operation_type="analysis",
        accepted_extensions=(".log", ".txt"),
        requires_confirmation=False,
        generates_report=True,
        generates_output_file=False,
        runner="analise_log_nfse",
    ),
    "autoexec-automation": ModuleDefinition(
        id="autoexec-automation",
        name="Autoexec Automation",
        description="Gera autoexec.sql a partir de XML NF-e autorizado.",
        operation_type="correction",
        accepted_extensions=(".xml", ".zip", ".rar"),
        requires_confirmation=False,
        generates_report=False,
        generates_output_file=True,
        runner="autoexec_automation",
    ),
    "reparo-base-firebird": ModuleDefinition(
        id="reparo-base-firebird",
        name="Reparo de Base Firebird",
        description="Executa validacao, backup e restore de base Firebird em copia isolada.",
        operation_type="repair",
        accepted_extensions=(".fdb", ".zip", ".rar"),
        requires_confirmation=True,
        generates_report=True,
        generates_output_file=True,
        runner="firebird_repair",
    ),
    "analise-xml-nfce": ModuleDefinition(
        id="analise-xml-nfce",
        name="Analise XML NFC-e",
        description="Processa XML NFC-e/NF-e e gera relatorio fiscal consolidado de totais e itens.",
        operation_type="analysis",
        accepted_extensions=(".xml", ".zip", ".rar"),
        requires_confirmation=False,
        generates_report=True,
        generates_output_file=False,
        runner="analise_xml_nfce",
    ),
    "ajuste-logos": ModuleDefinition(
        id="ajuste-logos",
        name="Ajuste de Logos",
        description="Recebe uma imagem e gera automaticamente as logos nos tamanhos exigidos.",
        operation_type="correction",
        accepted_extensions=(".bmp", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"),
        requires_confirmation=False,
        generates_report=False,
        generates_output_file=True,
        runner="logo_adjustment",
    ),
}


def list_modules() -> list[dict]:
    return [module.public_dict() for module in MODULES.values()]


def get_module(module_id: str) -> ModuleDefinition | None:
    return MODULES.get(module_id)
