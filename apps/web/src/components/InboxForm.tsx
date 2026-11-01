"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export function InboxForm({ onAdded }: { onAdded: () => void }) {
  const [text, setText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const resp = await api.post<{ result: string; video_id: number | null }>("/api/inbox", {
        text: text.trim(),
        source: "web",
      });
      setMessage(
        resp.result === "exists"
          ? `Уже в библиотеке (карточка #${resp.video_id}, счётчик +1)`
          : `Принято (#${resp.video_id}) — обработка на фоне`,
      );
      setText("");
      onAdded();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "ошибка");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="inbox-form" onSubmit={submit}>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Вставьте ссылку TikTok (vm.tiktok.com, /video/…, share-текст)"
        disabled={busy}
      />
      <button type="submit" disabled={busy}>
        {busy ? "…" : "Сохранить"}
      </button>
      {message && <span className="inbox-message">{message}</span>}
    </form>
  );
}
