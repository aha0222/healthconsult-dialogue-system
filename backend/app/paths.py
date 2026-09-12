"""后端侧路径定位：定位仓库根与 skill 目录。

默认按文件层级推导仓库根（backend/app/paths.py -> 仓库根）。
在 pip 安装、zip 打包、符号链接等非标准部署下该推导可能失效，
可用环境变量覆盖（也可写在仓库根 .env 中）：

    XIAONUAN_REPO_ROOT   仓库根目录
    XIAONUAN_SKILL_DIR   skill 目录（优先级高于 XIAONUAN_REPO_ROOT）

启动时调用 validate_paths() 可对关键文件做快速校验并给出清晰报错。
"""

import os
from pathlib import Path


def _default_repo_root() -> Path:
    # backend/app/paths.py -> backend/app -> backend -> 仓库根
    return Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """尝试从默认仓库根载入 .env，使路径覆盖也能写在 .env 里。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_default_repo_root() / ".env")


_load_dotenv()


def _resolve_repo_root() -> Path:
    override = os.environ.get("XIAONUAN_REPO_ROOT", "").strip()
    return Path(override) if override else _default_repo_root()


REPO_ROOT = _resolve_repo_root()


def _resolve_skill_dir() -> Path:
    override = os.environ.get("XIAONUAN_SKILL_DIR", "").strip()
    if override:
        return Path(override)
    return REPO_ROOT / "skills" / "healthconsult-assistant-skill"


SKILL_DIR = _resolve_skill_dir()
SKILL_MD = SKILL_DIR / "SKILL.md"
EXAMPLES_DIR = SKILL_DIR / "examples"


def validate_paths() -> None:
    """校验 skill 规范文件是否存在；缺失时抛出可定位问题的错误。"""
    if not SKILL_MD.is_file():
        raise FileNotFoundError(
            f"找不到 skill 规范文件：{SKILL_MD}。"
            "请确认仓库结构完整，或设置环境变量 XIAONUAN_SKILL_DIR "
            "指向包含 SKILL.md 的 skill 目录。"
        )
