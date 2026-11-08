"""The architecture gate must reject violations, not just pass today's tree."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/check_architecture.py"


def run_check(root):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_guard_rejects_cycles_sql_and_legacy_imports(tmp_path):
    package = tmp_path / "src/agent4pharma"
    (package / "modules/sample").mkdir(parents=True)
    (package / "modules/sample/a.py").write_text(
        'from .b import value\nfrom agent4pharma.store import Store\nquery = "SELECT id FROM jobs"\n',
        encoding="utf-8",
    )
    (package / "modules/sample/b.py").write_text("from .a import query\nvalue = 1\n", encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "import cycle" in result.stdout
    assert "legacy compatibility" in result.stdout
    assert "SQL belongs" in result.stdout


def test_guard_requests_review_without_failing_on_line_count_alone(tmp_path):
    package = tmp_path / "src/agent4pharma"
    package.mkdir(parents=True)
    source = package / "large.py"
    source.write_text("# explanatory line\n" * 401, encoding="utf-8")
    result = run_check(tmp_path)
    assert result.returncode == 0
    assert "REVIEW" in result.stdout
    source.write_text("# explanatory line\n" * 601, encoding="utf-8")
    assert run_check(tmp_path).returncode == 0
