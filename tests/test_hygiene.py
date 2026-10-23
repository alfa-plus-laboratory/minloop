"""Publishable files must not contain machine-specific absolute paths, and the core must stay
dependency-free (standard library only)."""
import ast
import re
import sys

from conftest import ROOT

SHIPPED = ["src", "tests", "examples", "docs", "probes", ".github", "README.md", "pyproject.toml"]
ABSOLUTE = re.compile(r"(?<![\w.])/(?:home|mnt|tmp|root|Users|var/folders)\b|[A-Za-z]:\\Users")


def shipped_files():
    for name in SHIPPED:
        path = ROOT / name
        for f in [path] if path.is_file() else sorted(path.rglob("*")) if path.exists() else []:
            if f.is_file() and "__pycache__" not in f.parts:
                yield f


def test_no_absolute_paths_in_shipped_files():
    hits = [f"{f.relative_to(ROOT)}:{i}" for f in shipped_files()
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if ABSOLUTE.search(line)]
    assert hits == []


def test_package_imports_only_the_standard_library():
    stdlib = set(sys.stdlib_module_names)
    for f in (ROOT / "src" / "minloop").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"), feature_version=(3, 10))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in stdlib, f"{f.name} imports {name}"
