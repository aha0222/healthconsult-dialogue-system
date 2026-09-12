"""路径定位模块测试：确认 skill 规范可被正确定位与校验。"""

import pytest

import backend.app.paths as paths


def test_validate_paths_passes_in_repo():
    paths.validate_paths()


def test_validate_paths_raises_when_skill_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SKILL_MD", tmp_path / "SKILL.md")
    with pytest.raises(FileNotFoundError):
        paths.validate_paths()


def test_skill_md_exists():
    assert paths.SKILL_MD.is_file()
