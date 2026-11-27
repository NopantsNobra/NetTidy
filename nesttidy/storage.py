from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .config import REPORT_DIR_NAME
from .model import OperationPlan


def default_db_path() -> Path:
    return Path.cwd() / REPORT_DIR_NAME / "history.sqlite"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path).resolve() if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("begin immediate")
        conn.execute(
            """
            create table if not exists task_history (
              plan_id text primary key,
              created_at text not null,
              mode text not null,
              selected_root text not null,
              output_root text,
              operation_count integer not null,
              issue_count integer not null,
              estimated_space integer not null,
              report_dir text,
              plan_json text not null,
              execution_mode text,
              execution_status text,
              transaction_log text,
              executed_at text
            )
            """
        )
        columns = {row[1] for row in conn.execute("pragma table_info(task_history)")}
        for column in ("execution_mode", "execution_status", "transaction_log", "executed_at"):
            if column not in columns:
                conn.execute(f"alter table task_history add column {column} text")
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def record_plan(plan: OperationPlan, report_dir: str | None = None, db_path: str | Path | None = None) -> None:
    # 完整 JSON 已写入独立报告；历史只保存索引。保留旧行的 plan_json，不迁移/删除旧数据。
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            insert into task_history
            (plan_id, created_at, mode, selected_root, output_root, operation_count, issue_count, estimated_space, report_dir, plan_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            on conflict(plan_id) do update set
              report_dir=coalesce(excluded.report_dir, task_history.report_dir),
              operation_count=excluded.operation_count, issue_count=excluded.issue_count
            """,
            (plan.plan_id, plan.created_at, plan.mode, plan.selected_root, plan.output_root,
             len(plan.operations), len(plan.issues), plan.estimated_space, report_dir),
        )
        conn.commit()


def record_execution(plan: OperationPlan, mode: str, status: str, transaction_log: str | None = None,
                     db_path: str | Path | None = None) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            insert into task_history
            (plan_id, created_at, mode, selected_root, output_root, operation_count, issue_count,
             estimated_space, plan_json, execution_mode, execution_status, transaction_log, executed_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?)
            on conflict(plan_id) do update set
              execution_mode=excluded.execution_mode, execution_status=excluded.execution_status,
              transaction_log=coalesce(excluded.transaction_log, task_history.transaction_log),
              executed_at=excluded.executed_at, issue_count=excluded.issue_count
            """,
            (plan.plan_id, plan.created_at, plan.mode, plan.selected_root, plan.output_root,
             len(plan.operations), len(plan.issues), plan.estimated_space, mode, status,
             transaction_log, datetime.now(UTC).isoformat()),
        )
        conn.commit()


def list_history(limit: int = 50, db_path: str | Path | None = None) -> list[dict]:
    with closing(connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select plan_id, created_at, mode, selected_root, output_root, operation_count, issue_count,
                   estimated_space, report_dir, execution_mode, execution_status, transaction_log, executed_at
            from task_history
            order by coalesce(executed_at, created_at) desc, plan_id
            limit ?
            """,
            (limit,),
        ).fetchall()
    result = [dict(row) for row in rows]
    for row in result:
        log = row["transaction_log"]
        try:
            row["undo_available"] = bool(log and Path(log).is_file())
        except OSError:
            row["undo_available"] = False
    return result
