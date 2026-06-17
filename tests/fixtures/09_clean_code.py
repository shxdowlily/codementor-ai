"""Чистый, корректный код - проверка на false positives."""
from typing import List


def average(values: List[float]) -> float:
    """Возвращает среднее арифметическое непустого списка чисел."""
    if not values:
        raise ValueError("values не может быть пустым")
    return sum(values) / len(values)


def is_palindrome(text: str) -> bool:
    """Проверяет, является ли строка палиндромом без учёта регистра."""
    normalized = text.lower()
    return normalized == normalized[::-1]
