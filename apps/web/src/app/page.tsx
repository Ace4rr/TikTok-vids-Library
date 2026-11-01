"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, getToken, setToken } from "@/lib/api";
import type { AiStatus, Filters, Folder, InboxItem, Tag, VideoDetail, VideoPage } from "@/lib/types";
import { InboxForm } from "@/components/InboxForm";
import { Sidebar } from "@/components/Sidebar";
import { VideoCard } from "@/components/VideoCard";
import { VideoModal } from "@/components/VideoModal";

const emptyFilters: Filters = { folderId: null, tag: null, viewed: null, favorite: null, q: "" };

export default function Home() {
  const [tokenReady, setTokenReady] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [feed, setFeed] = useState<VideoPage>({ items: [], total: 0 });
  const [folders, setFolders] = useState<Folder[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [selected, setSelected] = useState<VideoDetail | null>(null);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [inbox, setInbox] = useState<InboxItem[]>([]);

  const loadFeed = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filters.folderId) params.set("folder_id", String(filters.folderId));
      if (filters.tag) params.set("tag", filters.tag);
      if (filters.viewed !== null) params.set("viewed", String(filters.viewed));
      if (filters.favorite !== null) params.set("favorite", String(filters.favorite));
      if (filters.q) params.set("q", filters.q);
      params.set("limit", "100");
      setFeed(await api.get<VideoPage>(`/api/videos?${params.toString()}`));
      setAuthError(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setAuthError(error.message);
    }
  }, [filters]);

  const loadMeta = useCallback(async () => {
    try {
      const [f, t, a, i] = await Promise.all([
        api.get<Folder[]>("/api/folders"),
        api.get<Tag[]>("/api/tags"),
        api.get<AiStatus>("/api/system/ai"),
        api.get<InboxItem[]>("/api/inbox?limit=20"),
      ]);
      setFolders(f);
      setTags(t);
      setAiStatus(a);
      setInbox(i);
    } catch {
      /* метаданные не критичны */
    }
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") setTokenReady(true);
  }, []);

  useEffect(() => {
    if (!tokenReady) return;
    void loadFeed();
  }, [tokenReady, loadFeed]);

  useEffect(() => {
    if (!tokenReady) return;
    void loadMeta();
    // SSE: живые статусы обработки (docs/05 §5.5)
    const source = new EventSource(`/api/events?token=${encodeURIComponent(getToken())}`);
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as { kind: string };
        if (data.kind === "video") {
          void loadFeed();
          void loadMeta();
        }
      } catch {
        /* не-json кадр (ping) */
      }
    };
    return () => source.close();
  }, [tokenReady, loadFeed, loadMeta]);

  const openVideo = async (id: number) => {
    const detail = await api.get<VideoDetail>(`/api/videos/${id}`);
    setSelected(detail);
    if (!detail.viewed) await api.patch(`/api/videos/${id}`, { viewed: true });
  };

  const refreshAll = () => {
    void loadFeed();
    void loadMeta();
  };

  if (tokenReady && authError && !getToken()) {
    return (
      <main className="token-gate">
        <h1>TokLib</h1>
        <p>Введите токен доступа (TOKLIB_TOKEN сервера):</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setToken(tokenInput.trim());
            setAuthError(null);
            window.location.reload();
          }}
        >
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="токен"
            autoFocus
          />
          <button type="submit">Войти</button>
        </form>
      </main>
    );
  }

  return (
    <main className="app">
      <Sidebar
        folders={folders}
        tags={tags}
        aiStatus={aiStatus}
        inbox={inbox}
        filters={filters}
        onFilters={setFilters}
        onChanged={refreshAll}
      />
      <section className="content">
        <InboxForm onAdded={refreshAll} />
        <div className="feed-header">
          <h2>Библиотека <span className="muted">{feed.total}</span></h2>
          {filters.q && <span className="chip" onClick={() => setFilters({ ...filters, q: "" })}>поиск: {filters.q} ✕</span>}
        </div>
        <div className="grid">
          {feed.items.map((video) => (
            <VideoCard key={video.id} video={video} onOpen={() => void openVideo(video.id)} />
          ))}
        </div>
        {feed.items.length === 0 && (
          <p className="muted empty">
            Пусто. Вставьте ссылку TikTok выше или пришлите её Telegram-боту — карточка появится здесь через пару минут.
          </p>
        )}
      </section>
      {selected && (
        <VideoModal
          video={selected}
          folders={folders}
          onClose={() => {
            setSelected(null);
            refreshAll();
          }}
          onChanged={(detail) => setSelected(detail)}
        />
      )}
    </main>
  );
}
