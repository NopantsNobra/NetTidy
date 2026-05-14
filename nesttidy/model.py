from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Operation:
    op_id: str
    resource_root_id: str
    op_type: str
    source_path: str | None
    target_path: str | None
    rule_trace: list[str]
    risk_level: str = "low"
    status: str = "planned"
    precheck: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionIssue:
    issue_id: str
    path: str
    stage: str
    code: str
    message: str
    attempted_recovery: list[str] = field(default_factory=list)
    final_status: str = "open"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingEntry:
    entry_id: str
    resource_root_id: str
    entry_type: str
    original_path: str
    current_path: str
    planned_target_path: str
    operation_type: str
    rule_trace: list[str]
    risk_level: str
    status: str
    source_size: int | None
    source_mtime: int | None
    source_attributes: dict[str, Any] = field(default_factory=dict)
    name_cleaning_trace: list[str] = field(default_factory=list)
    conflict_resolution_trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OperationPlan:
    plan_id: str
    app_version: str
    rule_version: str
    mode: str
    selected_root: str
    output_root: str | None
    config_snapshot: dict[str, Any]
    source_snapshot_digest: str
    resource_roots: list[str]
    operations: list[Operation]
    issues: list[ExecutionIssue]
    mappings: list[MappingEntry]
    created_at: str
    frozen: bool = True
    estimated_space: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "app_version": self.app_version,
            "rule_version": self.rule_version,
            "mode": self.mode,
            "selected_root": self.selected_root,
            "output_root": self.output_root,
            "config_snapshot": self.config_snapshot,
            "source_snapshot_digest": self.source_snapshot_digest,
            "resource_roots": self.resource_roots,
            "operations": [op.to_dict() for op in self.operations],
            "issues": [issue.to_dict() for issue in self.issues],
            "mappings": [mapping.to_dict() for mapping in self.mappings],
            "created_at": self.created_at,
            "frozen": self.frozen,
            "estimated_space": self.estimated_space,
        }

