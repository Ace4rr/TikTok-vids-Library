// Зеркало pydantic-схем toklib_core.schemas (docs/09 §9.10: генерация из OpenAPI — позже).

export type VideoStatus =
  | "inbox"
  | "resolving"
  | "downloading"
  | "transcribing"
  | "analyzing"
  | "ready"
  | "error";

export type MediaMode = "none" | "embed" | "audio" | "file";
export type Health = "unknown" | "alive" | "dead";
export type InboxStatus = "new" | "resolved" | "duplicate" | "error";

export interface Tag {
  id: number;
  name: string;
  kind: "ai" | "user" | "system";
  source: "ai" | "user" | null;
  video_count?: number | null;
}

export interface Folder {
  id: number;
  name: string;
  parent_id: number | null;
  slug: string;
  icon: string | null;
}

export interface Note {
  id: number;
  video_id: number;
  kind: "user" | "ai_summary" | "key_idea";
  text: string;
  timestamp_sec: number | null;
  created_at: string;
  updated_at: string;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

export interface Transcript {
  video_id: number;
  language: string | null;
  model: string | null;
  source: "ai" | "user_edit";
  segments: TranscriptSegment[];
}

export interface Analysis {
  id: number;
  model: string;
  prompt_version: string;
  summary: string | null;
  key_idea: string | null;
  category: string | null;
  content_language: string | null;
  confidence: number | null;
  created_at: string;
}

export interface Video {
  id: number;
  tiktok_id: string | null;
  url: string;
  save_count: number;
  author_username: string | null;
  author_display: string | null;
  author_avatar_url: string | null;
  caption: string | null;
  hashtags: string[];
  duration_sec: number | null;
  cover_url: string | null;
  published_at: string | null;
  saved_at: string;
  status: VideoStatus;
  media_mode: MediaMode;
  health: Health;
  viewed: boolean;
  favorite: boolean;
  partial_ai: boolean;
  file_size: number | null;
  last_error: string | null;
}

export interface VideoDetail extends Video {
  tags: Tag[];
  folders: Folder[];
  notes: Note[];
  transcript: Transcript | null;
  analysis: Analysis | null;
}

export interface VideoPage {
  items: Video[];
  total: number;
}

export interface InboxResolveResponse {
  result: "created" | "exists" | "pending";
  video: Video | null;
  video_id: number | null;
  inbox_item_id: number | null;
}

export interface InboxItem {
  id: number;
  raw_text: string;
  url: string | null;
  source: string;
  status: InboxStatus;
  error: string | null;
  video_id: number | null;
  created_at: string;
}

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

export interface SearchResponse {
  total: number;
  items: { video: Video; rank: number | null }[];
}

export interface Filters {
  folderId: number | null;
  tag: string | null;
  viewed: boolean | null;
  favorite: boolean | null;
  q: string;
}
