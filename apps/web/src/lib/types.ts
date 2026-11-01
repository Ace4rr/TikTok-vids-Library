// Контракт API — генерируется из OpenAPI (src/lib/schema.d.ts ← openapi.json, docs/09 §9.10).
// Регенерация: uv run --all-packages python -m toklib_api.openapi_dump apps/web/openapi.json
//              затем npm run gen:api
// AiStatus и Filters оставлены hand-written: schema даёт dict[str, unknown] для providers.

import type { components } from "./schema";

type S = components["schemas"];

export type VideoStatus = S["VideoOut"]["status"];
export type MediaMode = S["VideoOut"]["media_mode"];
export type Health = S["VideoOut"]["health"];

export type Tag = S["TagOut"];
export type Folder = S["FolderOut"];
export type Note = S["NoteOut"];
export type TranscriptSegment = S["TranscriptSegmentModel"];
export type Transcript = S["TranscriptOut"];
export type Analysis = S["AnalysisOut"];
export type Video = S["VideoOut"];
export type VideoDetail = S["VideoDetail"];
export type VideoPage = S["VideoPage"];
export type InboxItem = S["InboxItemOut"];
export type SearchHit = S["SearchHit"];
export type SearchResponse = S["SearchResponse"];

export type InboxStatus = S["InboxItemOut"]["status"];

export interface AiStatus {
  mode: string;
  providers: {
    mode: string;
    stt: { provider: string; model: string; configured: boolean };
    llm: { provider: string; base_url: string; model: string; configured: boolean };
    embeddings: { provider: string; model: string; configured: boolean };
    night_window: { start: string; end: string };
  };
  budgets: Record<string, unknown>;
  queue_depths: Record<string, number>;
  dead_letters: number;
  download_success_24h: number | null;
}

export interface Filters {
  folderId: number | null;
  tag: string | null;
  viewed: boolean | null;
  favorite: boolean | null;
  q: string;
}

export interface InboxResolveResponse {
  result: "created" | "exists" | "pending";
  video: Video | null;
  video_id: number | null;
  inbox_item_id: number | null;
}
