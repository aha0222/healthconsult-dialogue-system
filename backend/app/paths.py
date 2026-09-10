"""后端侧路径定位：定位仓库根与 skill 目录。"""

from pathlib import Path

# backend/app/paths.py -> backend/app -> backend -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "healthconsult-assistant-skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
EXAMPLES_DIR = SKILL_DIR / "examples"
