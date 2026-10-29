"""Python structure via the standard-library AST (exact line numbers)."""

import ast

from app.services.parsers.base import CodeUnit

FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _start(node: ast.AST) -> int:
    # Decorators belong to the definition they decorate.
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


def parse_python(text: str) -> list[CodeUnit]:
    tree = ast.parse(text)  # SyntaxError -> caller falls back to line windows
    units: list[CodeUnit] = []
    previous_was_import = False

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if previous_was_import:
                units[-1].end_line = node.end_lineno
            else:
                units.append(CodeUnit("imports", None, node.lineno, node.end_lineno))
            previous_was_import = True
            continue
        previous_was_import = False

        if isinstance(node, FUNCTION_NODES):
            units.append(CodeUnit("function", node.name, _start(node), node.end_lineno))
        elif isinstance(node, ast.ClassDef):
            methods = [
                CodeUnit("method", child.name, _start(child), child.end_lineno, parent=node.name)
                for child in node.body
                if isinstance(child, FUNCTION_NODES)
            ]
            units.append(CodeUnit("class", node.name, _start(node), node.end_lineno, children=methods))
        # Anything else is top-level module code; the chunker covers those gaps.

    return units
