"""
static_analyzer.py

Детерминированный слой ревью кода: ходит по AST (Abstract Syntax Tree)
питоновского файла и ищет конкретные, заранее описанные паттерны проблем.

"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List


# Категории должны совпадать со словарём в tests/ground_truth.json
CATEGORIES = {
    "sql_injection",
    "eval_exec_usage",
    "hardcoded_secret",
    "mutable_default_arg",
    "bare_except",
    "broad_except",
    "bool_compare_none",
    "high_complexity",
    "shadowed_builtin",
    "long_line",
}


@dataclass
class Finding:
    category: str
    severity: str  # "critical" | "high" | "medium" | "low"
    line: int
    message: str
    source: str = "static"  # "static" | "llm"

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "line": self.line,
            "message": self.message,
            "source": self.source,
        }


_SECRET_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|api[_-]?key|token)\s*$", re.IGNORECASE
)
_SQL_EXECUTE_NAMES = {"execute", "executemany", "raw"}
_DANGEROUS_CALLS = {"eval", "exec"}
import builtins as _builtins_module

_BUILTIN_NAMES = set(dir(_builtins_module))


class StaticAnalyzer(ast.NodeVisitor):
    """Один проход по дереву файла, накопление находок в self.findings."""

    def __init__(self, source: str):
        self.source = source
        self.lines = source.splitlines()
        self.findings: List[Finding] = []
        # Лёгкий taint-tracking: имя переменной -> строка, где в неё
        # положили "подозрительную" динамическую строку. Без этого
        # анализатор не видит запрос, собранный отдельной строкой
        # ДО вызова .execute(query) — частый случай в реальном коде.
        self._tainted_vars: dict[str, int] = {}

    # ---------- правило 1: SQL-инъекция через f-string/конкатенацию ----------
    def visit_Call(self, node: ast.Call):
        func_name = self._call_name(node)

        if func_name in _SQL_EXECUTE_NAMES and node.args:
            arg = node.args[0]
            is_direct = self._looks_like_dynamic_sql(arg)
            is_via_variable = isinstance(arg, ast.Name) and arg.id in self._tainted_vars
            if is_direct or is_via_variable:
                self.findings.append(Finding(
                    category="sql_injection",
                    severity="critical",
                    line=node.lineno,
                    message=(
                        f"Вызов {func_name}(...) получает строку, собранную "
                        "через f-string/конкатенацию/.format(). Похоже на SQL-"
                        "инъекцию — используйте параметризованные запросы "
                        "(execute(query, params))."
                    ),
                ))

        if func_name in _DANGEROUS_CALLS:
            self.findings.append(Finding(
                category="eval_exec_usage",
                severity="critical",
                line=node.lineno,
                message=(
                    f"Использование {func_name}() на потенциально внешних "
                    "данных — произвольное выполнение кода. Замените на "
                    "ast.literal_eval() или явный парсинг."
                ),
            ))

        self.generic_visit(node)

    def _call_name(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    def _looks_like_dynamic_sql(self, node: ast.AST) -> bool:
        if isinstance(node, ast.JoinedStr):  # f-string
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return True
        if isinstance(node, ast.Call) and self._call_name(node) == "format":
            return True
        return False

    # ---------- правило 2: хардкод секретов ----------
    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and self._looks_like_dynamic_sql(node.value):
                self._tainted_vars[target.id] = node.lineno

            if isinstance(target, ast.Name) and _SECRET_KEY_PATTERN.search(target.id):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and node.value.value:
                    self.findings.append(Finding(
                        category="hardcoded_secret",
                        severity="high",
                        line=node.lineno,
                        message=(
                            f"Переменная '{target.id}' выглядит как секрет и "
                            "содержит строковый литерал прямо в коде. "
                            "Перенесите в переменные окружения (.env)."
                        ),
                    ))
        self.generic_visit(node)

    # ---------- правило 3: мутабельный дефолтный аргумент ----------
    def visit_FunctionDef(self, node: ast.FunctionDef):
        defaults = list(node.args.defaults) + list(node.args.kw_defaults)
        for default in defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.findings.append(Finding(
                    category="mutable_default_arg",
                    severity="high",
                    line=node.lineno,
                    message=(
                        f"Функция '{node.name}' использует мутабельный объект "
                        "(list/dict/set) как значение по умолчанию - он "
                        "будет общим между всеми вызовами. Используйте "
                        "None и создавайте объект внутри функции."
                    ),
                ))

        self._check_complexity(node)
        self.generic_visit(node)

    def _check_complexity(self, node: ast.FunctionDef):
        branch_nodes = (ast.If, ast.For, ast.While, ast.Try, ast.BoolOp)
        complexity = 1 + sum(
            1 for child in ast.walk(node) if isinstance(child, branch_nodes)
        )
        if complexity >= 10:
            self.findings.append(Finding(
                category="high_complexity",
                severity="medium",
                line=node.lineno,
                message=(
                    f"Функция '{node.name}' имеет высокую цикломатическую "
                    f"сложность (~{complexity}). Разбейте на более мелкие "
                    "функции - это снижает риск логических ошибок и "
                    "облегчает тестирование."
                ),
            ))

    # ---------- правило 4: bare except / except Exception ----------
    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        body_is_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)

        if node.type is None:
            self.findings.append(Finding(
                category="bare_except",
                severity="high",
                line=node.lineno,
                message=(
                    "Голый 'except:' перехватывает абсолютно всё, включая "
                    "KeyboardInterrupt и SystemExit, и маскирует реальные "
                    "ошибки. Укажите конкретный тип исключения."
                ),
            ))
        elif isinstance(node.type, ast.Name) and node.type.id == "Exception" and body_is_pass:
            self.findings.append(Finding(
                category="broad_except",
                severity="medium",
                line=node.lineno,
                message=(
                    "except Exception: pass - ошибка молча проглатывается. "
                    "Минимум залогируйте её (logging.exception(...))."
                ),
            ))
        self.generic_visit(node)

    # ---------- правило 5: сравнение с None через == ----------
    def visit_Compare(self, node: ast.Compare):
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)) and self._is_none(comparator):
                self.findings.append(Finding(
                    category="bool_compare_none",
                    severity="low",
                    line=node.lineno,
                    message=(
                        "Сравнение с None через ==/!=. PEP 8 рекомендует "
                        "'is None' / 'is not None' - это и быстрее, и "
                        "корректнее при перегруженном __eq__."
                    ),
                ))
        self.generic_visit(node)

    @staticmethod
    def _is_none(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    # ---------- правило 6: затенение builtin-имён ----------
    def visit_arg(self, node: ast.arg):
        if node.arg in _BUILTIN_NAMES and node.arg not in {"self", "cls"}:
            self.findings.append(Finding(
                category="shadowed_builtin",
                severity="low",
                line=node.lineno,
                message=(
                    f"Параметр '{node.arg}' затеняет встроенное имя Python. "
                    "Переименуйте, чтобы не терять доступ к оригинальной "
                    "функции/типу."
                ),
            ))
        self.generic_visit(node)

    # ---------- правило 7: длина строки ----------
    def check_long_lines(self, limit: int = 100):
        for i, line in enumerate(self.lines, start=1):
            if len(line) > limit:
                self.findings.append(Finding(
                    category="long_line",
                    severity="low",
                    line=i,
                    message=f"Строка длиннее {limit} символов ({len(line)}). Снижает читаемость.",
                ))


def analyze(source_code: str) -> List[dict]:
    """
    Главная точка входа слоя 1.
    Возвращает список находок в виде словарей (готово для JSON/агрегатора).
    При синтаксической ошибке возвращает одну находку с её описанием,
    не падая (важно - пользователь может прислать "сломанный" код).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        return [Finding(
            category="syntax_error",
            severity="critical",
            line=exc.lineno or 0,
            message=f"Код не парсится: {exc.msg}",
        ).to_dict()]

    analyzer = StaticAnalyzer(source_code)
    analyzer.visit(tree)
    analyzer.check_long_lines()
    # сортируем по строке для предсказуемого, читаемого вывода
    analyzer.findings.sort(key=lambda f: f.line)
    return [f.to_dict() for f in analyzer.findings]


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Использование: python static_analyzer.py <файл.py>")
        raise SystemExit(1)

    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    result = analyze(code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
