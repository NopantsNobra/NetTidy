from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import REPORT_DIR_NAME
from .model import OperationPlan


def default_db_path() -> Path:
    return Path.cwd() / REPORT_DIR_NAME / "history.sqlite"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path).resolve() if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
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
          plan_json text not null
        )
        """
    )
    conn.commit()
    return conn


def record_plan(plan: OperationPlan, report_dir: str | None = None, db_path: str | Path | None = None) -> None:
    data = plan.to_dict()
    with connect(db_path) as conn:
        conn.execute(
            """
            insert or replace into task_history
            (plan_id, created_at, mode, selected_root, output_root, operation_count, issue_count, estimated_space, report_dir, plan_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                plan.created_at,
                plan.mode,
                plan.selected_root,
                plan.output_root,
                len(plan.operations),
                len(plan.issues),
                plan.estimated_space,
                report_dir,
                json.dumps(data, ensure_ascii=False),
            ),
        )
        conn.commit()


def list_history(limit: int = 50, db_path: str | Path | None = None) -> list[dict]:
    with connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select plan_id, created_at, mode, selected_root, output_root, operation_count, issue_count, estimated_space, report_dir
            from task_history
            order by created_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]

