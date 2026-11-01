"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { AiStatus, Filters, Folder, InboxItem, Tag } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  inbox: "принято",
  resolving: "резолвится",
  downloading: "качается",
  transcribing: "транскрибируется",
  analyzing: "анализируется",
  ready: "готово",
  error: "ошибка",
};

export function Sidebar({
  folders,
  tags,
  aiStatus,
  inbox,
  filters,
  onFilters,
  onChanged,
}: {
  folders: Folder[];
  tags: Tag[];
  aiStatus: AiStatus | null;
  inbox: InboxItem[];
  filters: Filters;
  onFilters: (f: Filters) => void;
  onChanged: () => void;
}) {
  const [newFolder, setNewFolder] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [searchInput, setSearchInput] = useState("");

  const roots = useMemo(() => folders.filter((f) => f.parent_id === null), [folders]);
  const errors = inbox.filter((i) => i.status === "error");

  const createFolder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newFolder.trim()) return;
    await api.post("/api/folders", { name: newFolder.trim() });
    setNewFolder("");
    onChanged();
  };

  return (
    <aside className="sidebar">
      <h1 className="logo">TokLib</h1>

      <div className="search-box">
        <input
          value={showSearch ? searchInput : filters.q}
          placeholder="Поиск… author: tag: folder:"
          onFocus={() => setShowSearch(true)}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onFilters({ ...filters, q: searchInput });
            if (e.key === "Escape") {
              setSearchInput("");
              onFilters({ ...filters, q: "" });
            }
          }}
        />
      </div>

      <nav>
        <button className={classes(filters, null)} onClick={() => onFilters({ ...filters, folderId: null })}>
          Все видео
        </button>
        <button
          className={classes(filters, null, "viewed")}
          onClick={() => onFilters({ ...filters, viewed: filters.viewed === false ? null : false })}
        >
          🔵 Непросмотренные
        </button>
        <button
          className={classes(filters, null, "fav")}
          onClick={() => onFilters({ ...filters, favorite: filters.favorite === true ? null : true })}
        >
          ★ Избранное
        </button>
      </nav>

      <h3>Папки</h3>
      <nav>
        {roots.map((folder) => (
          <button
            key={folder.id}
            className={classes(filters, folder.id)}
            onClick={() => onFilters({ ...filters, folderId: filters.folderId === folder.id ? null : folder.id })}
          >
            {folder.icon || "📁"} {folder.name}
          </button>
        ))}
        {roots.length === 0 && <span className="muted small">нет папок</span>}
      </nav>
      <form className="folder-form" onSubmit={createFolder}>
        <input value={newFolder} onChange={(e) => setNewFolder(e.target.value)} placeholder="новая папка" />
      </form>

      <h3>Теги</h3>
      <div className="tag-cloud">
        {tags.slice(0, 30).map((tag) => (
          <span
            key={tag.id}
            className={`chip ${filters.tag === tag.name ? "active" : ""} ${tag.kind === "ai" ? "ai" : ""}`}
            onClick={() => onFilters({ ...filters, tag: filters.tag === tag.name ? null : tag.name })}
          >
            {tag.name}
            {tag.video_count ? ` ${tag.video_count}` : ""}
          </span>
        ))}
        {tags.length === 0 && <span className="muted small">теги появятся после обработки</span>}
      </div>

      {errors.length > 0 && (
        <>
          <h3>Ошибки ({errors.length})</h3>
          <ul className="inbox-errors">
            {errors.slice(0, 5).map((item) => (
              <li key={item.id}>{item.error || "ошибка обработки"}</li>
            ))}
          </ul>
        </>
      )}

      {aiStatus && (
        <div className="ai-status">
          <h3>AI · {aiStatus.mode}</h3>
          <div className="muted small">
            очередь: {Object.entries(aiStatus.queue_depths).map(([k, v]) => `${k}:${v}`).join(" ") || "—"} ·
            dead-letter: {aiStatus.dead_letters} ·
            скачивания 24ч: {aiStatus.download_success_24h === null ? "нет данных" : `${Math.round(aiStatus.download_success_24h * 100)}%`}
          </div>
        </div>
      )}
    </aside>
  );
}

function classes(filters: Filters, folderId: number | null, extra?: string) {
  const parts = ["nav-item"];
  if (extra === "viewed" && filters.viewed === false) parts.push("active");
  else if (extra === "fav" && filters.favorite === true) parts.push("active");
  else if (!extra && filters.folderId === folderId && folderId !== null) parts.push("active");
  else if (!extra && folderId === null && filters.folderId === null && !filters.tag) parts.push("active");
  return parts.join(" ");
}

export { STATUS_LABELS };
