"""Промпты LLM-анализа — docs/07 §7.5. Один вызов на видео, компактный системник."""

from __future__ import annotations

from .interfaces import AnalyzeInput

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = (
    "Ты — каталогизатор персональной видеотеки. По транскрипту и подписи короткого "
    "видео верни строго JSON по схеме. Без комментариев и markdown-обёрток. "
    "Транскрипт содержит ошибки распознавания — не копируй их в теги и текст. "
    "Саммари и ключевую идею пиши на языке транскрипта."
)

JSON_SCHEMA_HINT = (
    'Схема: {"tags": ["1–3 слова", 3–7 шт., преимущественно из словаря], '
    '"summary": "≤3 предложений, факты из транскрипта", '
    '"key_idea": "≤140 символов", "category": "из списка категорий", '
    '"content_language": "ISO-639-1"}'
)

# ~1500 токенов: обрезаем середину, сохраняя начало и конец (docs/07 §7.4 №4)
_MAX_TRANSCRIPT_CHARS = 6000


def truncate_transcript(text: str | None) -> str:
    if not text:
        return ""
    if len(text) <= _MAX_TRANSCRIPT_CHARS:
        return text
    half = _MAX_TRANSCRIPT_CHARS // 2
    return text[:half] + " […] " + text[-half:]


def build_user_prompt(inp: AnalyzeInput) -> str:
    parts = [
        f"Подпись: {inp.caption or '—'}",
        f"Хэштеги: {', '.join('#' + h.lstrip('#') for h in inp.hashtags) if inp.hashtags else 'нет'}",
        f"Теги библиотеки (предпочитай их): {', '.join(inp.tags_vocab) if inp.tags_vocab else 'нет'}",
        f"Категории: {'|'.join(inp.categories) if inp.categories else 'рецепт|туториал|лайфхак|фитнес|другое'}",
        f"Транскрипт: {truncate_transcript(inp.transcript_text) or 'нет (анализируй по подписи)'}",
        JSON_SCHEMA_HINT,
    ]
    return "\n".join(parts)


def build_repair_prompt(previous_raw: str, error: str) -> str:
    return (
        f"Предыдущий ответ не прошёл валидацию.\nОшибка: {error}\n"
        f"Предыдущий ответ: {previous_raw[:1500]}\n"
        "Верни исправленный строго JSON по схеме, без markdown и комментариев."
    )
