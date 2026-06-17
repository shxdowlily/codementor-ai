"""
aggregator.py

Главная точка входа основного (не-baseline) решения:
  1. прогоняет static_analyzer
  2. передаёт его находки в llm_reviewer как контекст
  3. объединяет результаты и считает итоговый score

Score - простая, объяснимая формула, не "магическое число":
100 баллов минус штраф за находку, штраф зависит от severity.
Любой может пересчитать на бумажке и проверить.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from . import static_analyzer
from . import llm_reviewer

SEVERITY_PENALTY = {
    "critical": 25,
    "high": 15,
    "medium": 8,
    "low": 3,
}


@dataclass
class ReviewReport:
    findings: List[dict]
    score: int
    static_count: int
    llm_count: int
    llm_error: str | None = None


def _score(findings: List[dict]) -> int:
    penalty = sum(SEVERITY_PENALTY.get(f["severity"], 5) for f in findings)
    return max(0, 100 - penalty)


def run_review(source_code: str, llm_mode: str = "mock") -> ReviewReport:
    static_findings = static_analyzer.analyze(source_code)
    llm_result = llm_reviewer.review(source_code, static_findings, mode=llm_mode)

    for f in llm_result.findings:
        f["source"] = "llm"

    all_findings = static_findings + llm_result.findings
    all_findings.sort(key=lambda f: f.get("line", 0))

    return ReviewReport(
        findings=all_findings,
        score=_score(all_findings),
        static_count=len(static_findings),
        llm_count=len(llm_result.findings),
        llm_error=llm_result.error,
    )
