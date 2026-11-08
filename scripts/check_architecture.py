"""Check source boundaries, import cycles and file growth without importing the app."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "agent4pharma"
LEGACY = {
    "agent",
    "catalog",
    "cli",
    "config",
    "connectors",
    "models",
    "registry",
    "store",
    "tools",
    "transport",
    "workflow",
}
SQL = re.compile(r"^\s*(SELECT\s|INSERT\s|UPDATE\s|DELETE\s|CREATE\s|ALTER\s|PRAGMA\s|BEGIN IMMEDIATE)", re.I)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str
    error: bool = True


def internal_imports(tree: ast.AST, module: str, is_package: bool) -> list[tuple[int, str]]:
    imports = []
    parent = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, parent)
            imports.append((node.lineno, base))
            imports.extend((node.lineno, f"{base}.{alias.name}") for alias in node.names)
    return [(line, name) for line, name in imports if name.startswith(PACKAGE + ".")]


def boundary_error(module: str, dependency: str) -> str | None:
    owner = module.split(".")[1:]
    target = dependency.split(".")[1:]
    if not owner or not target:
        return None
    if target[0] in LEGACY:
        return "import references a removed legacy compatibility module"
    forbidden = {
        "modules": {"infrastructure", "entrypoints", "composition", "agents", "workflows"},
        "runtime": {"modules", "infrastructure", "entrypoints", "composition", "agents", "workflows"},
        "agents": {"infrastructure", "entrypoints", "composition", "workflows", "runtime"},
        "workflows": {"infrastructure", "entrypoints", "composition"},
        "infrastructure": {"entrypoints", "composition", "agents", "workflows"},
        "entrypoints": {"infrastructure"},
    }
    if target[0] in forbidden.get(owner[0], set()):
        return f"{owner[0]} must not depend on {target[0]}"
    if owner[0] == "shared" and target[0] != "shared":
        return "shared must not depend on application packages"
    if owner[0] == target[0] == "modules" and len(owner) > 1 and len(target) > 2:
        if owner[1] != target[1] and target[2] not in {
            "collection_records",
            "document_reading",
            "evidence_catalog",
            "acquisition_interfaces",
            "batch_repository",
            "batch_service",
            "directory_repository",
            "material_identity",
            "paper_downloads",
            "venue_identity",
            "source_collection",
            "source_categories",
            "screening_policy",
            "intelligence_contracts",
            "management",
            "collection_plans",
            "sampling",
        }:
            return "cross-module import bypasses the public contract"
    return None


def import_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visited, active, cycles = set(), [], []

    def visit(module: str) -> None:
        if module in active:
            cycles.append(active[active.index(module) :] + [module])
            return
        if module in visited:
            return
        active.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        active.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return cycles


def inspect_source(root: Path) -> tuple[list[Finding], list[tuple[int, Path]]]:
    source = root / "src" / PACKAGE
    modules, sizes, findings = {}, [], []
    for path in sorted(source.rglob("*.py")):
        if path.parent == source and path.stem in LEGACY:
            findings.append(Finding(path, 1, "obsolete root forwarding module must not be recreated"))
        parts = path.relative_to(source.parent).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        text = path.read_text(encoding="utf-8")
        modules[name] = (path, ast.parse(text), path.name == "__init__.py")
        lines = len(text.splitlines())
        sizes.append((lines, path))
        if lines > 400:
            findings.append(
                Finding(
                    path, 1, f"review cohesion: file has {lines} lines (project review threshold 400)", False
                )
            )
    graph = {name: set() for name in modules}
    for directory in sorted((source / "modules").glob("*/")):
        if (directory / "__init__.py").exists() and not (directory / "README.md").exists():
            findings.append(Finding(directory, 1, "business module needs a README responsibility card"))
    if not modules:
        return [Finding(source, 1, "no Python source found; check --root")], []
    for module, (path, tree, package) in modules.items():
        seen = set()
        for line, dependency in internal_imports(tree, module, package):
            if problem := boundary_error(module, dependency):
                key = (line, problem)
                if key not in seen:
                    findings.append(Finding(path, line, f"{problem}: {dependency}"))
                    seen.add(key)
            target = dependency
            while target not in graph and "." in target:
                target = target.rpartition(".")[0]
            if target in graph and target != module:
                graph[module].add(target)
        persistence = ".infrastructure.persistence." in module
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                if length > 60:
                    findings.append(
                        Finding(path, node.lineno, f"review function {node.name}: {length} lines", False)
                    )
            if not persistence and isinstance(node, ast.Constant) and isinstance(node.value, str):
                if SQL.match(node.value):
                    findings.append(Finding(path, node.lineno, "SQL belongs in infrastructure.persistence"))
            if not persistence and isinstance(node, ast.Attribute) and node.attr == "db":
                findings.append(Finding(path, node.lineno, "raw database access outside persistence"))
    for cycle in import_cycles(graph):
        findings.append(Finding(modules[cycle[0]][0], 1, "import cycle: " + " -> ".join(cycle)))
    return findings, sorted(sizes, key=lambda item: (-item[0], str(item[1])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    findings, sizes = inspect_source(args.root.resolve())
    for finding in findings:
        level = "ERROR" if finding.error else "REVIEW"
        print(f"{level} {finding.path}:{finding.line}: {finding.message}")
    for lines, path in sizes[:5]:
        print(f"SIZE {lines:4} {path.relative_to(args.root.resolve())}")
    errors = sum(item.error for item in findings)
    print(f"Architecture: {len(sizes)} Python files, {errors} errors, {len(findings) - errors} review hints")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
