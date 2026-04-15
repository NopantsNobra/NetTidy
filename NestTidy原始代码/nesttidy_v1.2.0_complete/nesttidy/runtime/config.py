"""配置文件解析器：支持 TOML/YAML 格式的配置文件。 """
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List

from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG_NAMES = [
    "nesttidy.toml",
    "nesttidy.yaml",
    "nesttidy.yml",
    ".nesttidy",
]

# ══════════════════════════════════════════════════
# 【QC修复1 & 2】定义合法的枚举常量，从源头掐断幽灵配置
# ══════════════════════════════════════════════════
VALID_MODES = {"copy", "move"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass
class Config:
    """运行时配置。"""
    mode: str = "copy"
    max_workers: int = 4
    detect_duplicates: bool = True
    dry_run: bool = False
    force_cross_volume: bool = False
    output_dir: Optional[Path] = None
    log_file: Optional[Path] = None
    log_level: str = "INFO"
    exclude_patterns: List[str] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    max_nodes: int = 5_000_000
    verify_integrity: bool = True
    show_progress: bool = True

    def __post_init__(self):
        """后处理：类型转换与边界校验。"""
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.log_file, str):
            self.log_file = Path(self.log_file)
            
        # ══════════════════════════════════════════════════
        # 【QC修复1】模式白名单拦截
        # ══════════════════════════════════════════════════
        if self.mode not in VALID_MODES:
            raise ValueError(
                f"配置文件中的 mode 错误: {self.mode!r}，"
                f"当前版本仅支持: {VALID_MODES}"
            )
            
        # ══════════════════════════════════════════════════
        # 【QC修复2】日志级别拦截（防止静默失效）
        # ══════════════════════════════════════════════════
        if self.log_level.upper() not in VALID_LOG_LEVELS:
            raise ValueError(
                f"配置文件中的 log_level 错误: {self.log_level!r}，"
                f"合法值为: {VALID_LOG_LEVELS}"
            )
        self.log_level = self.log_level.upper() # 标准化大小写
            
        # ══════════════════════════════════════════════════
        # 【QC修复3】数值下限保护
        # ══════════════════════════════════════════════════
        if self.max_workers < 1:
            raise ValueError(f"配置文件中的 max_workers 必须 >= 1，收到: {self.max_workers}")
        if self.max_nodes < 1:
            raise ValueError(f"配置文件中的 max_nodes 必须 >= 1，收到: {self.max_nodes}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """从字典创建配置。"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered) # 触发 __post_init__ 进行校验

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return asdict(self)

    def merge(self, other: "Config") -> "Config":
        """合并两个配置（other 覆盖 self）。"""
        merged = self.to_dict()
        other_dict = other.to_dict()
        for key, value in other_dict.items():
            if value is not None:
                merged[key] = value
        return Config.from_dict(merged)


class ConfigLoader:
    """配置文件加载器。"""

    def __init__(self, search_paths: Optional[List[Path]] = None):
        self.search_paths = search_paths or []

    def load(self, config_path: Optional[Path] = None) -> Config:
        """加载配置。"""
        config = Config()
        if config_path:
            loaded = self._load_file(config_path)
            if loaded:
                config = config.merge(Config.from_dict(loaded))
        else:
            # 自动搜索配置文件
            for path in self._find_config_files():
                loaded = self._load_file(path)
                if loaded:
                    # 【QC修复4】日志脱敏：只打印文件名
                    logger.info(f"加载配置文件: {path.name} (来自 {path.parent.name})")
                    config = config.merge(Config.from_dict(loaded))
                    break
        return config

    def _find_config_files(self) -> List[Path]:
        """查找配置文件（严格按优先级排序）。"""
        # ══════════════════════════════════════════════════
        # 【QC修复5】显式构建优先级链，防止隐式追加导致优先级错乱
        # ══════════════════════════════════════════════════
        search_locations = []
        
        # 优先级 1：显式指定的搜索路径
        search_locations.extend(self.search_paths)
        
        # 优先级 2：当前工作目录 (CWD)
        search_locations.append(Path.cwd())
        
        # 优先级 3：用户主目录 (兜底)
        search_locations.append(Path.home())

        found = []
        for location in search_locations:
            if not location.exists():
                continue
            for name in DEFAULT_CONFIG_NAMES:
                path = location / name
                if path.exists():
                    found.append(path)
                    # 通常我们只需要最高优先级的那个，但如果同目录下有 
                    # nesttidy.toml 和 nesttidy.yaml，按 DEFAULT_CONFIG_NAMES 顺序取先发现的
        return found

    def _load_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """加载单个配置文件。"""
        if not path.exists():
            return None
        suffix = path.suffix.lower()
        try:
            if suffix == ".toml":
                return self._load_toml(path)
            elif suffix in (".yaml", ".yml"):
                return self._load_yaml(path)
            else:
                try:
                    return self._load_toml(path)
                except Exception:
                    return self._load_yaml(path)
        except Exception as exc:
            logger.warning(f"解析配置文件失败 ({path.name}): {exc}")
            return None

    def _load_toml(self, path: Path) -> Dict[str, Any]:
        """加载 TOML 配置文件。"""
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(path, "rb") as f:
            data = tomllib.load(f)
        if "tool" in data and "nesttidy" in data["tool"]:
            return data["tool"]["nesttidy"]
        elif "nesttidy" in data:
            return data["nesttidy"]
        else:
            return data

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        """加载 YAML 配置文件。"""
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML 未安装，无法加载 YAML 配置文件")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) # 审查通过：安全加载，防注入
        if data is None:
            return {}
        if "nesttidy" in data:
            return data["nesttidy"]
        else:
            return data


def load_config(
    config_path: Optional[Path] = None,
    **overrides,
) -> Config:
    """加载配置的便捷函数。"""
    loader = ConfigLoader()
    config = loader.load(config_path)
    if overrides:
        override_config = Config.from_dict(overrides)
        config = config.merge(override_config)
    return config
