"""
llm_reviewer.py

Слой 2: глубокий ревью кода с помощью LLM. Принимает исходный код плюс
находки статического анализатора (как контекст) и просит модель найти
то, что статика структурно не видит: логические ошибки, неудачные
названия, архитектурные замечания.

Два режима работы - это осознанное архитектурное решение, не костыль:

  mode="mock"  - детерминированная заглушка. Нужна, чтобы:
                 (а) тестировать весь пайплайн и evaluate.py без сети и
                     без расхода бесплатных лимитов API;
                 (б) CI/демонстрация работала даже без ключа.
  mode="live"  - реальный вызов бесплатного API (Groq, OpenAI-совместимый
                 эндпоинт). Требует GROQ_API_KEY в .env.

BASELINE_PROMPT - намеренно слабый, "наивный" промпт без структуры и без
контекста от статического анализатора. Используется только в baseline.py,
чтобы честно показать разницу между "просто спросили LLM" и основным
гибридным решением.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Имя модели задаётся через .env (GROQ_MODEL), потому что бесплатные модели
# у Groq периодически меняются/переименовываются — список актуальных моделей
# смотри на https://console.groq.com/docs/models перед первым live-запуском.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

LLM_CATEGORIES = {
    "logic_bug", "naming", "architecture", "performance",
    "sql_injection", "eval_exec_usage", "hardcoded_secret",
    "mutable_default_arg", "bare_except", "broad_except", "other",
}

SYSTEM_PROMPT = """Ты — опытный senior-разработчик, проводящий код-ревью для junior-разработчика.
Тебе дан Python-код и список находок статического анализатора (могут быть пустыми).
Твоя задача — найти то, что статический анализ НЕ видит: логические ошибки,
проблемы в названиях переменных/функций, архитектурные замечания, проблемы
производительности. Не повторяй находки статического анализатора буквально,
если не хочешь дать к ним дополнительный контекст.

Ответь СТРОГО в формате JSON-массива объектов, без markdown, без пояснений вне JSON:
[{"category": "logic_bug|naming|architecture|performance|other",
  "severity": "critical|high|medium|low",
  "line": <int>,
  "message": "<конкретное объяснение по-русски, 1-2 предложения>"}]

Если проблем не нашёл — верни пустой массив []."""

BASELINE_PROMPT = "Посмотри на этот код и скажи, есть ли в нём проблемы."


@dataclass
class LLMReviewResult:
    findings: List[dict]
    raw_response: str
    mode: str
    error: Optional[str] = None


def _mock_response(source_code: str, static_findings: List[dict]) -> List[dict]:
    
    findings = []
    lines = source_code.splitlines()

    # простая, прозрачная "имитация интуиции": ищем типичные паттерны логики,
    # которые в реальном вызове модель находит регулярно (off-by-one, range())
    for i, line in enumerate(lines, start=1):
        if re.search(r"range\(\s*len\(", line):
            findings.append({
                "category": "naming",
                "severity": "low",
                "line": i,
                "message": "[MOCK] range(len(...)) — рассмотрите enumerate() для читаемости.",
            })
        if re.search(r"==\s*True|==\s*False", line):
            findings.append({
                "category": "other",
                "severity": "low",
                "line": i,
                "message": "[MOCK] Сравнение с True/False избыточно, используйте сам булевый выход.",
            })

    return findings


def review(source_code: str, static_findings: List[dict], mode: str = "mock") -> LLMReviewResult:
    if mode == "mock":
        findings = _mock_response(source_code, static_findings)
        return LLMReviewResult(findings=findings, raw_response="<mock>", mode="mock")

    if mode != "live":
        raise ValueError(f"Неизвестный режим: {mode}")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return LLMReviewResult(
            findings=[], raw_response="", mode="live",
            error="GROQ_API_KEY не найден в окружении. Добавьте его в .env (см. .env.example).",
        )
    if requests is None:
        return LLMReviewResult(
            findings=[], raw_response="", mode="live",
            error="Пакет 'requests' не установлен (pip install -r requirements.txt).",
        )

    user_message = (
        f"Находки статического анализатора:\n{json.dumps(static_findings, ensure_ascii=False)}\n\n"
        f"Код для ревью:\n```python\n{source_code}\n```"
    )

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.1,
                "max_tokens": 1500,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = _safe_parse_json_array(content)
        return LLMReviewResult(findings=parsed, raw_response=content, mode="live")
    except Exception as exc:  # сетевые ошибки, таймауты, невалидный JSON от модели
        return LLMReviewResult(findings=[], raw_response="", mode="live", error=str(exc))


def review_baseline(source_code: str, mode: str = "mock") -> str:
    """
    Наивный baseline: без структуры, без static-контекста, без JSON-схемы.
    Возвращает свободный текст, как и должен возвращать "просто вызов API".
    """
    if mode == "mock":
        return "[MOCK BASELINE] Код выглядит рабочим, явных проблем не замечено."

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or requests is None:
        return "[ошибка baseline] нет API-ключа или requests"

    resp = requests.post(
        GROQ_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "user", "content": f"{BASELINE_PROMPT}\n\n{source_code}"},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _safe_parse_json_array(text: str) -> List[dict]:
    """Модели иногда оборачивают JSON в ```json ... ``` - подчищаем перед парсингом."""
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
