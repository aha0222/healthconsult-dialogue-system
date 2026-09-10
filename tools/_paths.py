"""统一路径定位。

所有 tools/ 下的脚本通过本模块定位仓库根与 skill 目录，
避免各自写死 `parent.parent / "SKILL.md"` 这类脆弱路径。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "healthconsult-assistant-skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
EXAMPLES_DIR = SKILL_DIR / "examples"
RESULTS_DIR = REPO_ROOT / "tests" / "results"
