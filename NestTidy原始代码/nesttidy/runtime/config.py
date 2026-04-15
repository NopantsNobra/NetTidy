"""配置文件解析器：支持 TOML/YAML 格式的配置文件。

配置优先级：
  1. 命令行参数（最高）
  2. 配置文件
  3. 默认值（最低）
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List

from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

# 默认配置文件名
DEFAULT_CONFIG_NAMES = [
    "nesttidy.toml",
    "nesttidy.yaml",
    "nesttidy.yml",
    ".nesttidy",
]


@dataclass
class Config:
    """运行时配置。"""

    # 执行模式
    mode: str = "copy"  # copy | move | auto

    # 并发设置
    max_workers: int = 4

    # 功能开关
    detect_duplicates: bool = True
    dry_run: bool = False
    force_cross_volume: bool = False

    # 输出设置
    output_dir: Optional[Path] = None
    log_file: Optional[Path] = None
    log_level: str = "INFO"

    # 过滤设置
    exclude_patterns: List[str] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)

    # 安全设置
    max_nodes: int = 5_000_000  # 最大节点数（熔断阈值）
    verify_integrity: bool = True  # 跨卷移动时是否校验完整性

    # 进度设置
    show_progress: bool = True

    def __post_init__(self):
        """后处理：类型转换。"""
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.log_file, str):
            self.log_file = Path(self.log_file)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """从字典创建配置。"""
        # 过滤掉未知字段
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

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
        """
        Args:
            search_paths: 配置文件搜索路径列表
        """
        self.search_paths = search_paths or []

    def load(self, config_path: Optional[Path] = None) -> Config:
        """加载配置。

        Args:
            config_path: 显式指定的配置文件路径

        Returns:
            Config: 配置对象
        """
        config = Config()

        # 尝试加载配置文件
        if config_path:
            loaded = self._load_file(config_path)
            if loaded:
                config = config.merge(Config.from_dict(loaded))
        else:
            # 自动搜索配置文件
            for path in self._find_config_files():
                loaded = self._load_file(path)
                if loaded:
                    logger.info(f"加载配置文件: {path}")
                    config = config.merge(Config.from_dict(loaded))
                    break

        return config

    def _find_config_files(self) -> List[Path]:
        """查找配置文件。"""
        found = []

        # 当前目录
        cwd = Path.cwd()
        for name in DEFAULT_CONFIG_NAMES:
            path = cwd / name
            if path.exists():
                found.append(path)

        # 用户主目录
        home = Path.home()
        for name in DEFAULT_CONFIG_NAMES:
            path = home / name
            if path.exists():
                found.append(path)

        # 搜索路径
        for search_path in self.search_paths:
            for name in DEFAULT_CONFIG_NAMES:
                path = search_path / name
                if path.exists():
                    found.append(path)

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
                # 尝试 TOML 格式
                try:
                    return self._load_toml(path)
                except Exception:
                    return self._load_yaml(path)
        except Exception as exc:
            logger.warning(f"加载配置文件失败: {path} — {exc}")
            return None

    def _load_toml(self, path: Path) -> Dict[str, Any]:
        """加载 TOML 配置文件。"""
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            import tomli as tomllib  # 第三方库

        with open(path, "rb") as f:
            data = tomllib.load(f)

        # 提取 [tool.nesttidy] 或 [nesttidy] 段
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
            data = yaml.safe_load(f)

        if data is None:
            return {}

        # 提取 nesttidy 段
        if "nesttidy" in data:
            return data["nesttidy"]
        else:
            return data


def load_config(
    config_path: Optional[Path] = None,
    **overrides,
) -> Config:
    """加载配置的便捷函数。

    Args:
        config_path: 配置文件路径
        **overrides: 命令行覆盖参数

    Returns:
        Config: 配置对象
    """
    loader = ConfigLoader()
    config = loader.load(config_path)

    # 应用命令行覆盖
    if overrides:
        override_config = Config.from_dict(overrides)
        config = config.merge(override_config)

    return config
