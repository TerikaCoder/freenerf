import shutil
import pytest

from freenerf.tools.filesystem import FileSystem


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "backups").mkdir()

    (tmp_path / "src" / "active.py").write_text(
        "needle in active source\\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "active_test.py").write_text(
        "needle in test\\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "needle in documentation\\n", encoding="utf-8"
    )
    (tmp_path / "patch_script.py").write_text(
        "needle in root script\\n", encoding="utf-8"
    )
    (tmp_path / "backups" / "old.py").write_text(
        "needle in backup\\n", encoding="utf-8"
    )
    (tmp_path / "src" / "old.py.bak").write_text(
        "needle in backup file\\n", encoding="utf-8"
    )

    return FileSystem(tmp_path)


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep unavailable")
def test_source_scope(project):
    result = project.search("needle", "source")

    assert "active.py" in result
    assert "active_test.py" in result
    assert "README.md" in result
    assert "patch_script.py" not in result
    assert "backups" not in result
    assert ".bak" not in result


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep unavailable")
def test_project_scope(project):
    result = project.search("needle", "project")

    assert "active.py" in result
    assert "patch_script.py" in result
    assert "backups" not in result
    assert ".bak" not in result


def test_empty_query(project):
    assert project.search("   ") == "Query is empty"


def test_invalid_scope(project):
    assert "Invalid scope" in project.search("needle", "invalid")


@pytest.mark.parametrize("protected", [".git/config", ".freenerf/tasks.db"])
def test_delete_protects_project_metadata_and_recovery_data(project, protected):
    target = project.root / protected
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("keep me", encoding="utf-8")

    with pytest.raises(PermissionError, match="protected"):
        project.delete_file(protected)

    assert target.read_text(encoding="utf-8") == "keep me"


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep unavailable")
def test_no_results(project):
    assert project.search("not-present") == "No results"


def test_tree_respects_depth_and_skips_internal_data(project):
    (project.root / ".git").mkdir()
    (project.root / ".git" / "config").write_text("secret", encoding="utf-8")
    (project.root / ".freenerf").mkdir()
    (project.root / ".freenerf" / "tasks.db").write_text("db", encoding="utf-8")
    (project.root / "src" / "nested").mkdir()
    (project.root / "src" / "nested" / "deep.py").write_text("x", encoding="utf-8")

    result = project.tree(".", depth=2)

    assert "active.py" in result
    assert "nested/" in result
    assert "deep.py" not in result
    assert ".git" not in result
    assert ".freenerf" not in result
