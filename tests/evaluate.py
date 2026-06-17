"""
evaluate.py

Главный скрипт оценки проекта. Запускает на всех файлах из
tests/fixtures/ два решения:

  baseline: один наивный вызов LLM без структуры (llm_reviewer.review_baseline)
  main:     гибрид static_analyzer + llm_reviewer (aggregator.run_review)

...и сравнивает обнаруженные категории с tests/ground_truth.json,
считая per-category и общий precision/recall/F1.

Запуск:
    python -m tests.evaluate --mode mock     # без сети, для разработки
    python -m tests.evaluate --mode live     # с реальным GROQ_API_KEY

Результат сохраняется в tests/results/metrics.json и печатается таблицей.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from reviewer import aggregator, llm_reviewer

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.json"
RESULTS_PATH = Path(__file__).parent / "results" / "metrics.json"

# Ключевые слова для оценки baseline (он отдаёт свободный текст, не JSON,
# поэтому единственный честный способ понять "заметил ли он проблему X" —
# поискать релевantные слова в его ответе). Это эвристика, и она прямо
# называется эвристикой в отчёте — не выдаём её за точный метод.
BASELINE_KEYWORDS = {
    "sql_injection": ["sql", "инъек", "injection", "f-string", "query"],
    "eval_exec_usage": ["eval", "exec", "произвольн"],
    "hardcoded_secret": ["секрет", "пароль", "password", "api key", "ключ", "hardcode"],
    "mutable_default_arg": ["mutable", "мутабел", "default", "дефолт"],
    "bare_except": ["except", "исключени"],
    "broad_except": ["except", "исключени", "проглат"],
    "high_complexity": ["сложност", "complexity", "вложен"],
    "logic_bug": ["логич", "off-by-one", "баг", "ошибк"],
    "syntax_error": ["синтакс", "syntax"],
}


def load_ground_truth() -> dict:
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))["fixtures"]


def predicted_categories_main(source_code: str, llm_mode: str) -> set[str]:
    report = aggregator.run_review(source_code, llm_mode=llm_mode)
    return {f["category"] for f in report.findings}


def predicted_categories_baseline(source_code: str, llm_mode: str) -> set[str]:
    text = llm_reviewer.review_baseline(source_code, mode=llm_mode).lower()
    found = set()
    for category, keywords in BASELINE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            found.add(category)
    return found


def prf1(true_positive: int, false_positive: int, false_negative: int) -> tuple[float, float, float]:
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return round(precision, 3), round(recall, 3), round(f1, 3)


def evaluate_solution(name: str, predictor, ground_truth: dict, llm_mode: str) -> dict:
    tp = fp = fn = 0
    per_file = {}

    for filename, gt in ground_truth.items():
        if filename.startswith("_"):
            continue
        source = (FIXTURES_DIR / filename).read_text(encoding="utf-8")
        expected = set(gt["expected"])
        predicted = predictor(source, llm_mode)

        file_tp = len(expected & predicted)
        file_fp = len(predicted - expected)
        file_fn = len(expected - predicted)

        tp += file_tp
        fp += file_fp
        fn += file_fn

        per_file[filename] = {
            "expected": sorted(expected),
            "predicted": sorted(predicted),
            "tp": file_tp, "fp": file_fp, "fn": file_fn,
        }

    precision, recall, f1 = prf1(tp, fp, fn)
    return {
        "solution": name,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp, "fp": fp, "fn": fn,
        "per_file": per_file,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    args = parser.parse_args()

    ground_truth = load_ground_truth()

    main_result = evaluate_solution(
        "main_hybrid_static_plus_llm", predicted_categories_main, ground_truth, args.mode
    )

    if args.mode == "live":
        baseline_result = evaluate_solution(
            "baseline_naive_llm", predicted_categories_baseline, ground_truth, args.mode
        )
        results = {"mode": args.mode, "baseline": baseline_result, "main": main_result}
    else:
        # ВАЖНО: в mock-режиме baseline_naive_llm() возвращает статичную
        # заглушку-строку, а не реальный ответ модели. Считать по ней
        # precision/recall было бы нечестным сравнением - методичка прямо
        # запрещает "намеренно сломанный baseline". Поэтому в mock-режиме
        # мы baseline вообще не оцениваем, только проверяем, что пайплайн
        # main-решения работает корректно. Реальное сравнение baseline vs
        # main делается только в --mode live, на настоящем API.
        results = {"mode": args.mode, "baseline": None, "main": main_result}

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nРежим: {args.mode}\n")
    print(f"{'Решение':<28}{'Precision':<12}{'Recall':<12}{'F1':<8}")
    print("-" * 60)
    rows = [main_result] if args.mode == "mock" else [results["baseline"], main_result]
    for r in rows:
        print(f"{r['solution']:<28}{r['precision']:<12}{r['recall']:<12}{r['f1']:<8}")

    print(f"\nДетали сохранены в {RESULTS_PATH}")

    if args.mode == "mock":
        print(
            "\n[!] Mock-режим валидирует только пайплайн main-решения (без сети, без "
            "трат лимита API). Baseline здесь НЕ показан намеренно: в mock-режиме он "
            "возвращает статичную заглушку, и оценивать её было бы нечестным сравнением. "
            "Честные числа baseline vs main - только через 'python -m tests.evaluate "
            "--mode live' с реальным GROQ_API_KEY, и именно они идут в Final Report."
        )
        print(
            "[!] Также: logic_bug (файл 08) принципиально не виден static-слою - это "
            "ожидаемо, не баг (см. README, раздел 'Ограничения')."
        )


if __name__ == "__main__":
    main()
