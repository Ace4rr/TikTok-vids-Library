"use client";

import { mediaUrl } from "@/lib/api";
import type { Video } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  inbox: "принято",
  resolving: "резолвится",
  downloading: "качается",
  transcribing: "транскрибируется",
  analyzing: "анализируется",
  ready: "готово",
  error: "ошибка",
};

export function VideoCard({ video, onOpen }: { video: Video; onOpen: () => void }) {
  const processing = !["ready", "error"].includes(video.status);
  return (
    <article className={`card ${video.viewed ? "viewed" : ""}`} onClick={onOpen}>
      <div className="thumb">
        {video.media_mode === "file" ? (
          <img src={mediaUrl(`/api/media/${video.id}/cover`)} alt="" loading="lazy" />
        ) : (
          <div className="thumb-embed">▶ embed</div>
        )}
        {video.duration_sec !== null && <span className="duration">{formatDuration(video.duration_sec)}</span>}
        {video.favorite && <span className="fav-star">★</span>}
      </div>
      <div className="card-body">
        <p className="caption">{video.caption || "(без подписи)"}</p>
        <p className="meta">
          @{video.author_username || "—"}
          {video.save_count > 1 && <span className="muted"> · сохранено ×{video.save_count}</span>}
        </p>
        <div className="card-badges">
          <span className={`status s-${video.status}`}>{processing ? "⏳ " : ""}{STATUS_LABELS[video.status] ?? video.status}</span>
          {video.partial_ai && <span className="status s-partial">частично (Tier-0)</span>}
          {video.health === "dead" && <span className="status s-dead">мёртвая ссылка</span>}
        </div>
      </div>
    </article>
  );
}

export function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
