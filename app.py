"""
app.py

Веб-интерфейс CodeMentor AI на Gradio. Запуск:
    python app.py
Откроется локально на http://127.0.0.1:7860, плюс можно получить
публичную shareable-ссылку (share=True) - удобно для демо без деплоя
и для запуска прямо в Google Colab.

Дизайн интерфейса намеренно простой: одна страница, два таба.
  Tab 1 "Ревью кода" - основной сценарий использования.
  Tab 2 "Сравнение с baseline" - наглядно показывает разницу между
        наивным вызовом LLM и гибридным решением. Этот таб существует
        специально для защиты проекта: легче показать на живом примере,
        чем объяснять словами.
"""

from __future__ import annotations

import os
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from reviewer import aggregator, llm_reviewer, static_analyzer

load_dotenv()

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"

SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}


def _current_mode() -> str:
    return "live" if os.environ.get("GROQ_API_KEY") else "mock"


def _load_examples() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(p.name for p in FIXTURES_DIR.glob("*.py"))


def load_example(filename: str) -> str:
    if not filename:
        return ""
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_main_review(code: str):
    if not code or not code.strip():
        return "—", [], "Вставьте код или выберите пример слева."

    mode = _current_mode()
    report = aggregator.run_review(code, llm_mode=mode)

    rows = [
        [SEVERITY_EMOJI.get(f["severity"], ""), f["category"], f["line"], f["message"], f["source"]]
        for f in report.findings
    ]

    status = f"Режим LLM-слоя: **{mode}**"
    if mode == "mock":
        status += " (нет GROQ_API_KEY → используется офлайн-заглушка, см. README)"
    if report.llm_error:
        status += f"\n\n⚠️ LLM-слой вернул ошибку: {report.llm_error}\nПоказаны только находки статического анализа."

    return f"{report.score} / 100", rows, status


def run_comparison(code: str):
    if not code or not code.strip():
        return "Вставьте код для сравнения.", "—"

    mode = _current_mode()
    static_findings = static_analyzer.analyze(code)
    main_report = aggregator.run_review(code, llm_mode=mode)
    baseline_text = llm_reviewer.review_baseline(code, mode=mode)

    main_summary = (
        f"**Найдено проблем: {len(main_report.findings)}** "
        f"(static: {main_report.static_count}, llm: {main_report.llm_count})\n\n"
        + "\n".join(f"- {SEVERITY_EMOJI.get(f['severity'], '')} `{f['category']}` (строка {f['line']}): {f['message']}"
                     for f in main_report.findings)
    )
    return baseline_text, main_summary or "Проблем не найдено."


with gr.Blocks(title="CodeMentor AI") as demo:
    gr.Markdown(
        "# CodeMentor AI\n"
        "Гибридный ревью Python-кода: детерминированный статический анализ "
        "(свой AST-движок) + LLM-слой для логических и архитектурных замечаний.\n\n"
        f"Текущий режим LLM: **{_current_mode()}**"
        + ("" if _current_mode() == "live" else " — добавьте GROQ_API_KEY в .env для реального ревью.")
    )

    with gr.Tab("Ревью кода"):
        with gr.Row():
            with gr.Column(scale=1):
                example_dropdown = gr.Dropdown(
                    choices=_load_examples(), label="Загрузить пример из тестового набора"
                )
                code_input = gr.Code(language="python", label="Код для ревью", lines=20)
                review_btn = gr.Button("Проверить код", variant="primary")
            with gr.Column(scale=1):
                score_output = gr.Textbox(label="Итоговый score")
                status_output = gr.Markdown()
                findings_output = gr.Dataframe(
                    headers=["", "Категория", "Строка", "Сообщение", "Источник"],
                    label="Найденные проблемы",
                    wrap=True,
                )

        example_dropdown.change(load_example, inputs=example_dropdown, outputs=code_input)
        review_btn.click(run_main_review, inputs=code_input, outputs=[score_output, findings_output, status_output])

    with gr.Tab("Сравнение с baseline"):
        gr.Markdown(
            "Baseline - это **наивный** вызов LLM без структуры, без схемы и без "
            "контекста от статического анализа (`'Посмотри на этот код и скажи, есть "
            "ли в нём проблемы.'`). Сравнение наглядно показывает, зачем нужен гибридный "
            "подход, а не просто 'спросить модель'."
        )
        compare_input = gr.Code(language="python", label="Код для сравнения", lines=15)
        compare_btn = gr.Button("Сравнить")
        with gr.Row():
            baseline_out = gr.Markdown(label="Baseline (наивный промпт)")
            main_out = gr.Markdown(label="Main solution (гибрид)")
        compare_btn.click(run_comparison, inputs=compare_input, outputs=[baseline_out, main_out])

    with gr.Tab("Ограничения"):
        gr.Markdown(
            "### Что система НЕ умеет (честно)\n"
            "- Поддерживается только **Python** - других языков нет.\n"
            "- Статический слой ловит конкретные, заранее описанные паттерны, "
            "а не произвольные баги - это набор правил, а не полноценный linter.\n"
            "- LLM-слой может давать ложноположительные срабатывания и иногда "
            "указывать неверный номер строки (типичная проблема генеративных моделей).\n"
            "- Taint-tracking для SQL-инъекций - облегчённый (по одной функции), "
            "не отслеживает поток данных между функциями/модулями.\n"
            "- Бесплатный тир LLM-API имеет лимиты по скорости запросов - при "
            "пакетной проверке многих файлов подряд возможны задержки.\n"
            "- Метрики precision/recall посчитаны на собственном контролируемом "
            "наборе из 13 файлов, а не на большом независимом датасете - это "
            "оценка качества системы, а не строгая статистическая гарантия "
            "на любом чужом коде."
        )


if __name__ == "__main__":
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
    )