"use client";

import { useState } from "react";
import { api, mediaUrl } from "@/lib/api";
import type { Folder, VideoDetail } from "@/lib/types";

export function VideoModal({
  video: initial,
  folders,
  onClose,
  onChanged,
}: {
  video: VideoDetail;
  folders: Folder[];
  onClose: () => void;
  onChanged: (detail: VideoDetail) => void;
}) {
  const [video, setVideo] = useState<VideoDetail>(initial);
  const [noteText, setNoteText] = useState("");
  const [showTranscript, setShowTranscript] = useState(false);

  const apply = (detail: VideoDetail) => {
    setVideo(detail);
    onChanged(detail);
  };

  const patch = async (body: Record<string, unknown>) => {
    apply(await api.patch<VideoDetail>(`/api/videos/${video.id}`, body));
  };

  const reanalyze = async () => {
    await api.post(`/api/videos/${video.id}/reanalyze`, { force: true });
    apply(await api.get<VideoDetail>(`/api/videos/${video.id}`));
  };

  const retry = async () => {
    await api.post(`/api/system/retry/${video.id}`, {});
    apply(await api.get<VideoDetail>(`/api/videos/${video.id}`));
  };

  const addNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!noteText.trim()) return;
    await api.post(`/api/videos/${video.id}/notes`, { text: noteText.trim() });
    setNoteText("");
    apply(await api.get<VideoDetail>(`/api/videos/${video.id}`));
  };

  const hasFile = video.media_mode === "file" && video.tiktok_id;
  const embedId = video.tiktok_id;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>✕</button>

        <div className="player">
          {hasFile ? (
            <video src={mediaUrl(`/api/media/${video.id}/file`)} controls poster={mediaUrl(`/api/media/${video.id}/cover`)} />
          ) : embedId ? (
            <iframe
              src={`https://www.tiktok.com/embed/v2/${embedId}`}
              allow="autoplay; encrypted-media; fullscreen"
              title={video.caption || "TikTok"}
            />
          ) : (
            <div className="player-empty">видео ещё резолвится…</div>
          )}
        </div>

        <div className="modal-body">
          <div className="modal-actions">
            <button onClick={() => patch({ favorite: !video.favorite })}>{video.favorite ? "★ в избранном" : "☆ в избранное"}</button>
            <button onClick={() => patch({ viewed: !video.viewed })}>{video.viewed ? "✓ просмотрено" : "отметить просмотр"}</button>
            <button onClick={() => void reanalyze()}>↻ AI-анализ</button>
            {(video.status === "error" || video.partial_ai) && <button onClick={() => void retry()}>⟳ Повторить этап</button>}
            {video.url && (
              <a className="button-link" href={video.url} target="_blank" rel="noreferrer">
                Открыть в TikTok
              </a>
            )}
            <select
              value={video.folders[0]?.id ?? ""}
              onChange={async (e) => {
                const folderId = e.target.value ? Number(e.target.value) : null;
                await patch({ folder_ids: folderId ? [folderId] : [] });
              }}
            >
              <option value="">— без папки —</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>{f.name}</option>
              ))}
            </select>
          </div>

          <p className="caption">{video.caption || "(без подписи)"}</p>
          <p className="meta">
            @{video.author_username || "—"} · {video.status}
            {video.partial_ai && " · частично (Tier-0)"}
            {video.save_count > 1 && ` · ×${video.save_count}`}
          </p>
          {video.last_error && <p className="error-text">Ошибка: {video.last_error}</p>}

          {video.analysis && (
            <div className="analysis">
              {video.analysis.key_idea && <p className="key-idea">💡 {video.analysis.key_idea}</p>}
              {video.analysis.summary && <p className="summary">{video.analysis.summary}</p>}
              <p className="meta">модель: {video.analysis.model} · категория: {video.analysis.category ?? "—"}</p>
            </div>
          )}

          <div className="tag-row">
            {video.tags.map((tag) => (
              <span key={tag.id} className={`chip ${tag.kind === "ai" ? "ai" : ""}`}>{tag.name}</span>
            ))}
            {video.hashtags.map((h) => (
              <span key={`h-${h}`} className="chip muted">#{h}</span>
            ))}
          </div>

          {video.transcript && (
            <div className="transcript">
              <button className="link" onClick={() => setShowTranscript(!showTranscript)}>
                {showTranscript ? "Скрыть транскрипт" : `Транскрипт (${video.transcript.language ?? "?"}, ${video.transcript.model ?? "правлен"})`}
              </button>
              {showTranscript && (
                <div className="transcript-segments">
                  {video.transcript.segments.map((seg, i) => (
                    <p key={i}>
                      <span className="muted">[{formatTs(seg.start)}]</span> {seg.text}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}

          <h4>Заметки</h4>
          <ul className="notes">
            {video.notes.map((note) => (
              <li key={note.id} className={note.kind !== "user" ? "ai-note" : ""}>{note.text}</li>
            ))}
          </ul>
          <form className="note-form" onSubmit={addNote}>
            <input value={noteText} onChange={(e) => setNoteText(e.target.value)} placeholder="заметка: зачем я это сохранил?" />
            <button type="submit">+</button>
          </form>
        </div>
      </div>
    </div>
  );
}

function formatTs(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
