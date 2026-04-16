"""执行策略模块：提供不同的文件操作策略。

策略模式（Strategy Pattern）：
  - ExecutionStrategy: 抽象基类
  - CopyStrategy: 纯复制策略
  - SameVolMoveStrategy: 同卷极速移动策略
  - CrossVolMoveStrategy: 跨卷安全搬迁策略
"""
from nesttidy.safety.strategies.base import (
    ExecutionStrategy,
    ExecutionResult,
    ExecutionSummary,
)
from nesttidy.safety.strategies.copy_strategy import CopyStrategy, copy_strategy
from nesttidy.safety.strategies.same_vol_move import SameVolMoveStrategy, same_vol_move_strategy
from nesttidy.safety.strategies.cross_vol_move import CrossVolMoveStrategy, cross_vol_move_strategy

__all__ = [
    "ExecutionStrategy",
    "ExecutionResult",
    "ExecutionSummary",
    "CopyStrategy",
    "copy_strategy",
    "SameVolMoveStrategy",
    "same_vol_move_strategy",
    "CrossVolMoveStrategy",
    "cross_vol_move_strategy",
]
