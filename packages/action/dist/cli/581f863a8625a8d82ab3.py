from __future__ import annotations

import ast
import json
import pathlib
import sys
from dataclasses import asdict, dataclass


EXCLUDED = {".git", ".venv", "venv", "__pycache__", "build", "dist", ".tox"}


@dataclass
class Symbol:
    id: str
    file: str
    kind: str
    exported: bool
    text: str


class Collector(ast.NodeVisitor):
    def __init__(self, source: str, relative_path: str) -> None:
        self.source = source
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.symbols: list[Symbol] = []
        self.imports: list[str] = []
        self.routes: list[str] = []

    def _record(self, node: ast.AST, name: str, kind: str) -> None:
        qualified = ".".join([*self.scope, name])
        segment = ast.get_source_segment(self.source, node) or ""
        self.symbols.append(
            Symbol(
                id=f"{self.relative_path}#{qualified}",
                file=self.relative_path,
                kind=kind,
                exported=not name.startswith("_") and len(self.scope) == 0,
                text=segment,
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.scope else "function"
        self._record(node, node.name, kind)
        for decorator in node.decorator_list:
            rendered = ast.unparse(decorator)
            if any(marker in rendered for marker in (".get(", ".post(", ".put(", ".delete(", ".route(")):
                self.routes.append(f"{self.relative_path}#{node.name}")
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, node.name, "class")
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module)


def analyze(root: pathlib.Path) -> dict[str, object]:
    symbols: list[dict[str, object]] = []
    imports: dict[str, list[str]] = {}
    routes: list[str] = []
    tests: list[str] = []
    effects: set[str] = {"return", "exception", "stdout", "stderr"}
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED for part in path.parts):
            continue
        relative_path = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError:
            continue
        collector = Collector(source, relative_path)
        collector.visit(tree)
        symbols.extend(asdict(symbol) for symbol in collector.symbols)
        imports[relative_path] = collector.imports
        routes.extend(collector.routes)
        if "test" in path.name.lower() or "tests" in path.parts:
            tests.append(relative_path)
        if any(marker in source for marker in ("requests.", "httpx.", "aiohttp.")):
            effects.add("outbound_http")
        if any(marker in source for marker in ("open(", "Path(", ".write_text(", ".unlink(")):
            effects.add("filesystem")
        if any(marker in source.lower() for marker in ("execute(", "select(", "insert(", "update(")):
            effects.add("database")
        if collector.routes:
            effects.add("http")
    return {
        "symbols": symbols,
        "imports": imports,
        "routes": routes,
        "tests": tests,
        "effects": sorted(effects),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: analyze.py ROOT")
    print(json.dumps(analyze(pathlib.Path(sys.argv[1]).resolve()), separators=(",", ":")))
