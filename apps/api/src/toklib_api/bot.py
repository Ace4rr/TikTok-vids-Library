"""Telegram-бот — MVP-канал приёма ссылок + уведомления (docs/06 Р4, FR-1.2).

Отдельный процесс: `python -m toklib_api.bot`. Long polling для приёма ссылок
и фоновая подписка на SSE `/api/events` для уведомлений о готовности/ошибках.

Кому слать уведомления: чаты из TOKLIB_TG_ALLOWED (через запятую); если список
пуст — чатам, которые хоть раз писали боту в этом запуске.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
from toklib_core.normalize import LinkError, parse_link

log = logging.getLogger(__name__)


def _api_url() -> str:
    return os.environ.get("TOKLIB_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _api_token() -> str | None:
    return os.environ.get("TOKLIB_TOKEN") or None


def _allowed_ids() -> set[int]:
    raw = os.environ.get("TOKLIB_TG_ALLOWED", "").strip()
    if not raw:
        return set()
    return {int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()}


def _headers() -> dict:
    token = _api_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def notification_text(event: dict, detail: dict | None) -> str | None:
    """Текст уведомления по SSE-событию видео; None — уведомлять не о чем.

    Чистая функция — покрыта тестом (events API не несёт прежнего статуса,
    поэтому переходы отслеживает сам бот).
    """
    if event.get("kind") != "video":
        return None
    status = str(event.get("status", ""))
    health = str(event.get("health", ""))
    title = "?"
    author = ""
    if detail:
        caption = (detail.get("caption") or "(без подписи)").strip()
        title = caption[:120] + ("…" if len(caption) > 120 else "")
        author = f" (@{detail['author_username']})" if detail.get("author_username") else ""
    vid = event.get("video_id", "?")
    if health == "dead":
        return f"💀 Мёртвая ссылка — видео #{vid} удалено/приватное{author}. Локальная копия: embed."
    if status == "ready":
        return f"✅ Готово: {title}{author}"
    if status == "error":
        return f"⚠️ Ошибка обработки видео #{vid}: {title}{author} — повтор из веб-панели."
    return None


async def _fetch_detail(http: httpx.AsyncClient, video_id: int) -> dict | None:
    try:
        resp = await http.get(f"{_api_url()}/api/videos/{video_id}", headers=_headers())
        return resp.json() if resp.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def notifications_loop(bot, known_chats: set[int], stop: asyncio.Event) -> None:
    """SSE-подписка: видео готово / ошибка / мёртвая ссылка → сообщение в чаты."""
    last_status: dict[int, str] = {}
    recipients = _allowed_ids() | known_chats
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as http:
        while not stop.is_set():
            try:
                async with http.stream(
                    "GET", f"{_api_url()}/api/events", params={"token": _api_token() or ""}, headers=_headers()
                ) as stream:
                    async for line in stream.aiter_lines():
                        if stop.is_set():
                            return
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[len("data: ") :])
                        except ValueError:
                            continue
                        video_id = event.get("video_id")
                        status = str(event.get("status", ""))
                        if event.get("kind") != "video" or not isinstance(video_id, int):
                            continue
                        if last_status.get(video_id) == status:
                            continue
                        last_status[video_id] = status
                        if not recipients:
                            continue
                        detail = await _fetch_detail(http, video_id)
                        text = notification_text(event, detail)
                        if not text:
                            continue
                        for chat_id in recipients:
                            try:
                                await bot.send_message(chat_id, text)
                            except Exception as exc:  # noqa: BLE001 — телеграм может быть недоступен
                                log.warning("уведомление в чат %s не доставлено: %s", chat_id, exc)
            except httpx.HTTPError as exc:
                log.warning("SSE-поток оборвался (%s) — переподключение через 5 с", exc)
                await asyncio.sleep(5)


def run_bot() -> None:
    try:
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import Command
        from aiogram.types import Message
    except ImportError as exc:
        raise SystemExit("aiogram не установлен: uv sync --all-packages --extra bot (apps/api)") from exc

    bot_token = os.environ.get("TOKLIB_TG_BOT_TOKEN", "").strip()
    if not bot_token:
        raise SystemExit("TOKLIB_TG_BOT_TOKEN не задан")

    bot = Bot(bot_token)
    dp = Dispatcher()
    allowed = _allowed_ids()
    known_chats: set[int] = set()
    stop = asyncio.Event()

    def _guard(message: Message) -> bool:
        return not allowed or message.from_user.id in allowed

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if not _guard(message):
            return
        known_chats.add(message.chat.id)
        await message.answer(
            "TokLib: пришли мне ссылку на видео TikTok — оно появится в библиотеке.\n"
            "/status — состояние очереди. О готовности видео пришлю уведомление сюда."
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

    async def main() -> None:
        notifier = asyncio.create_task(notifications_loop(bot, known_chats, stop))
        try:
            await dp.start_polling(bot)
        finally:
            stop.set()
            notifier.cancel()

    print("toklib bot: long polling + уведомления запущены")
    asyncio.run(main())


if __name__ == "__main__":
    run_bot()
