from __future__ import annotations

"""api_server must survive being imported.

py_compile checks syntax, so it happily accepts a module-level statement that
references a function defined further down the file - which then fails at
import with NameError. That is exactly what happened on 2026-08-26: a registry
was constructed ten lines above its factory, the compile passed, the commit
landed, and the pipeline simply did not come back after the restart.

The rest of the suite deliberately never imports api_server, because doing so
boots every scheduler and opens the live database (see tests/test_gateway.py).
So nothing was watching for this. These checks are STATIC - they read the
module's syntax tree and never execute it.
"""

import ast
from pathlib import Path

API_SERVER = Path(__file__).resolve().parent.parent / "api_server.py"
TREE = ast.parse(API_SERVER.read_text(encoding="utf-8"), filename=str(API_SERVER))


def _module_level_definitions(tree):
    """Name -> line where each top-level def/class/assignment is bound."""
    defined: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.setdefault(node.name, node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.setdefault(target.id, target.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.setdefault(node.target.id, node.target.lineno)
    return defined


def ordering_problems(tree):
    """Module-level statements that use a name bound further down the file."""
    defined = _module_level_definitions(tree)
    problems = []
    for node in tree.body:
        # Only executable module-level statements can trip on ordering;
        # function and class bodies run long after the module has loaded.
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom),
        ):
            continue
        # Names inside a lambda are deferred to call time, like a function body.
        deferred = {
            id(name)
            for lam in ast.walk(node)
            if isinstance(lam, ast.Lambda)
            for name in ast.walk(lam)
        }
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Name) or not isinstance(inner.ctx, ast.Load):
                continue
            if id(inner) in deferred:
                continue
            bound_at = defined.get(inner.id)
            if bound_at is not None and bound_at > inner.lineno:
                problems.append(
                    f"line {inner.lineno} uses {inner.id!r}, defined at line {bound_at}"
                )
    return problems


def test_the_detector_catches_the_bug_it_exists_for() -> None:
    """A guard nobody has watched fail is a guard nobody should trust.

    This is the exact shape that stopped the pipeline: something built at
    module level from a factory defined below it. Syntactically valid,
    NameError at import.
    """
    bad = ast.parse(
        "REGISTRY = build(factory)\n"
        "\n"
        "def factory():\n"
        "    return 1\n"
    )

    problems = ordering_problems(bad)

    assert problems, "the detector missed a forward reference at module level"
    assert "factory" in problems[0]


def test_a_name_used_inside_a_lambda_is_not_flagged() -> None:
    # Deferred until called, so ordering is irrelevant. Flagging it would make
    # the guard noisy enough that someone eventually deletes it.
    fine = ast.parse(
        "REGISTRY = build(lambda: helper())\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
    )

    assert ordering_problems(fine) == []


def test_a_function_body_may_reference_anything() -> None:
    fine = ast.parse(
        "def early():\n"
        "    return late()\n"
        "\n"
        "def late():\n"
        "    return 1\n"
    )

    assert ordering_problems(fine) == []


def test_api_server_has_no_module_level_forward_references() -> None:
    problems = ordering_problems(TREE)

    assert not problems, (
        "module-level code references names defined later - api_server will "
        "raise NameError on import and the pipeline will not start:\n  "
        + "\n  ".join(problems)
    )
