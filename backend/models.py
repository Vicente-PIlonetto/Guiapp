from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import time

OperationType = Literal["analysis", "validation", "correction", "repair"]
JobStatus = Literal["pending", "processing", "completed", "error"]


@dataclass(frozen=True)
class ModuleDefinition:
    id: str
    name: str
    description: str
    operation_type: OperationType
    accepted_extensions: tuple[str, ...]
    requires_confirmation: bool
    generates_report: bool
    generates_output_file: bool
    runner: str
    enabled: bool = True
    disabled_reason: str | None = None

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "operation_type": self.operation_type,
            "accepted_extensions": list(self.accepted_extensions),
            "requires_confirmation": self.requires_confirmation,
            "generates_report": self.generates_report,
            "generates_output_file": self.generates_output_file,
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
        }


@dataclass
class Job:
    id: str
    module_id: str
    original_filename: str
    status: JobStatus = "pending"
    logs: list[str] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    report_path: Path | None = None
    output_path: Path | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_log(self, message: str) -> None:
        self.logs.append(message)
        self.updated_at = time.time()

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "module_id": self.module_id,
            "original_filename": self.original_filename,
            "status": self.status,
            "logs": self.logs[-30:],
            "result": self.result,
            "error": self.error,
            "has_report": bool(self.report_path and self.report_path.exists()),
            "has_output": bool(self.output_path and self.output_path.exists()),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
