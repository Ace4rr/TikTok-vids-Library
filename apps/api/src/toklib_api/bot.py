"""Telegram-бот — MVP-канал приёма ссылок (docs/06 Р4, FR-1.2).

Отдельный процесс: `python -m toklib_api.bot`. Долгий polling, шлёт ссылки
в API; уведомления о готовности — следующая итерация (подписка на SSE).
"""

from __future__ import annotations

import os

from toklib_core.normalize import LinkError, parse_link


def _api_url() -> str:
    return os.environ.get("TOKLIB_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _api_token() -> str | None:
    return os.environ.get("TOKLIB_TOKEN") or None


def _allowed_ids() -> set[int]:
    raw = os.environ.get("TOKLIB_TG_ALLOWED", "").strip()
    if not raw:
        return set()
    return {int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()}


def run_bot() -> None:
    try:
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import Command
        from aiogram.types import Message
    except ImportError as exc:
        raise SystemExit("aiogram не установлен: uv sync --extra bot (apps/api)") from exc

    bot_token = os.environ.get("TOKLIB_TG_BOT_TOKEN", "").strip()
    if not bot_token:
        raise SystemExit("TOKLIB_TG_BOT_TOKEN не задан")

    import httpx

    bot = Bot(bot_token)
    dp = Dispatcher()
    allowed = _allowed_ids()

    def _guard(message: Message) -> bool:
        return not allowed or message.from_user.id in allowed

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if not _guard(message):
            return
        await message.answer(
            "TokLib: пришли мне ссылку на видео TikTok — оно появится в библиотеке.\n"
            "/status — состояние очереди."
        )

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not _guard(message):
            return
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(f"{_api_url()}/api/system/ai", headers=_headers())
        if resp.status_code != 200:
            await message.answer(f"API недоступен ({resp.status_code})")
            return
        data = resp.json()
        depths = ", ".join(f"{k}: {v}" for k, v in data.get("queue_depths", {}).items()) or "пусто"
        ratio = data.get("download_success_24h")
        ratio_text = f"{ratio * 100:.0f}%" if ratio is not None else "нет данных"
        await message.answer(
            f"Режим AI: {data.get('mode')}\nОчередь — {depths}\n"
            f"Dead-letter: {data.get('dead_letters', 0)}\n"
            f"Успешных скачиваний за 24ч: {ratio_text}"
        )

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        if not _guard(message):
            return
        try:
            parse_link(message.text or "")
        except LinkError as exc:
            await message.answer(f"Это не похоже на ссылку TikTok: {exc}")
            return
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{_api_url()}/api/inbox",
                json={"text": message.text, "source": "tg"},
                headers=_headers(),
            )
        if resp.status_code == 400:
            await message.answer(f"Отклонено: {resp.json().get('detail', '?')}")
            return
        body = resp.json()
        if body.get("result") == "exists":
            await message.answer(f"Уже в библиотеке (карточка #{body.get('video_id')}, счётчик +1).")
        else:
            await message.answer(f"Принято, обработка началась (карточка #{body.get('video_id')}).")

    print("toklib bot: long polling запущен")
    import asyncio

    asyncio.run(dp.start_polling(bot))


def _headers() -> dict:
    token = _api_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


if __name__ == "__main__":
    run_bot()
