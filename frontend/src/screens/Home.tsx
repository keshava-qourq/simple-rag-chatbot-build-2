import React from "react";

import * as UI from "@/lib/ui";
import { Icons } from "@/lib/icons";
import { brand } from "@/lib/brand";
import { apiFetch, apiUpload, askDocument } from "@/lib/api";
import type {
  ConfigStatusResponse,
  DocumentCreateResponse,
  DocumentStatus,
  DocumentStatusResponse,
} from "@/lib/api";

const { Input, Label } = UI;

const TYPE_LABEL: Record<string, string> = { pdf: "PDF", docx: "DOCX", txt: "TXT" };

const STATUS_STYLE: Record<DocumentStatus, { label: string; bg: string; fg: string }> = {
  ready: { label: "Ready", bg: "#E3EDE8", fg: "#285146" },
  processing: { label: "Processing", bg: "#EFE7D6", fg: "#5E5238" },
  failed: { label: "Failed", bg: "#F6E1DA", fg: "#8A3524" },
};

const POLL_MS = 3000;

type MessageRole = "user" | "assistant" | "notice";

interface Message {
  id: string;
  role: MessageRole;
  text: string;
  time: string;
  source?: string | null;
}

interface Pending {
  docId: string;
}

type BtnKind = "primary" | "quiet" | "plain";

const Btn = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind }
>(function Btn({ kind = "quiet", className = "", style = {}, children, ...rest }, ref) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-45 disabled:cursor-not-allowed";
  const kinds: Record<BtnKind, string> = {
    primary: "px-4 py-2 text-white hover:opacity-90",
    quiet: "px-3 py-2 border hover:bg-white",
    plain: "px-2 py-1 hover:underline",
  };
  const kindStyle: React.CSSProperties =
    kind === "primary"
      ? { backgroundColor: "#2F5D50" }
      : kind === "quiet"
        ? { borderColor: "#DED2BD", color: "#3A342C", backgroundColor: "rgba(255,255,255,0.6)" }
        : { color: "#2F5D50" };
  return (
    <button
      ref={ref}
      type={rest.type || "button"}
      className={`${base} ${kinds[kind]} ${className}`}
      style={{ ...kindStyle, ...style }}
      {...rest}
    >
      {children}
    </button>
  );
});

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function validateFile(file: File): string | null {
  const lower = file.name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot >= 0 ? lower.slice(dot + 1) : "";
  if (!["pdf", "docx", "txt"].includes(ext)) {
    return `“${file.name}” is not a supported file type. Reading Room accepts PDF, DOCX and TXT files only.`;
  }
  const sizeMB = file.size / (1024 * 1024);
  if (sizeMB > 20) {
    return `“${file.name}” is ${sizeMB.toFixed(1)} MB. Files must be roughly 20 MB or smaller.`;
  }
  return null;
}

/** A freshly-created document (from `POST /documents`) does not carry
 * `error_message`/`chunk_count` yet; fill both in so the optimistic row
 * never renders `undefined`/`NaN`. */
function toLibraryRow(created: DocumentCreateResponse): DocumentStatusResponse {
  return {
    id: created.id,
    file_name: created.file_name,
    file_type: created.file_type,
    status: created.status,
    error_message: null,
    chunk_count: 0,
    created_at: created.created_at,
  };
}

/** Human label for a source reference: pages for PDFs, chunk numbers
 * otherwise (mirrors backend/app/schemas.py `SourceReference`, where exactly
 * one of the two is set). */
function sourceLabel(source: { page_number: number | null; chunk_index: number | null } | null): string | null {
  if (!source) return null;
  if (source.page_number != null) return `Page ${source.page_number}`;
  if (source.chunk_index != null) return `Chunk ${source.chunk_index}`;
  return null;
}

export default function Screen() {
  const [docs, setDocs] = React.useState<DocumentStatusResponse[]>([]);
  const [docsLoading, setDocsLoading] = React.useState(true);
  const [docsError, setDocsError] = React.useState<string | null>(null);
  const [threads, setThreads] = React.useState<Record<string, Message[]>>({});
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [question, setQuestion] = React.useState("");
  const [pending, setPending] = React.useState<Pending | null>(null);
  const [uploadError, setUploadError] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<DocumentStatusResponse | null>(null);
  const [deleteError, setDeleteError] = React.useState<string | null>(null);
  const [config, setConfig] = React.useState<ConfigStatusResponse | null>(null);
  const [configLoading, setConfigLoading] = React.useState(true);

  const confirmRef = React.useRef<HTMLButtonElement | null>(null);
  const fileRef = React.useRef<HTMLInputElement | null>(null);
  const logRef = React.useRef<HTMLDivElement | null>(null);

  const mountedRef = React.useRef(true);
  const pollTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const sortedDocs = React.useMemo(
    () =>
      [...docs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [docs],
  );
  const selected = docs.find((d) => d.id === selectedId) || null;
  const thread = (selected && threads[selected.id]) || [];
  const canAsk = !!selected && selected.status === "ready" && !pending;

  function clearPollTimer() {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  // Polling only runs while a document is still processing (AC: polling
  // stops once the library has settled, and resumes after a fresh upload
  // reintroduces a "processing" row).
  function schedulePollIfNeeded(data: DocumentStatusResponse[]) {
    clearPollTimer();
    if (data.some((d) => d.status === "processing")) {
      pollTimerRef.current = setTimeout(() => {
        refreshDocuments();
      }, POLL_MS);
    }
  }

  async function refreshDocuments() {
    try {
      const data = await apiFetch<DocumentStatusResponse[]>("/documents");
      if (!mountedRef.current) return;
      setDocs(data);
      setDocsError(null);
      schedulePollIfNeeded(data);
    } catch (err) {
      if (mountedRef.current) {
        setDocsError(err instanceof Error ? err.message : "Could not load the document library.");
      }
    } finally {
      if (mountedRef.current) setDocsLoading(false);
    }
  }

  // Initial load of the document library.
  React.useEffect(() => {
    mountedRef.current = true;
    refreshDocuments();
    return () => {
      mountedRef.current = false;
      clearPollTimer();
    };
    // Runs once on mount; refreshDocuments/clearPollTimer are stable across
    // the component's lifetime in behaviour even though they're redefined
    // each render, and re-running this effect on every render would create
    // a parallel polling loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Configuration banner reflects the backend, not local state.
  React.useEffect(() => {
    let cancelled = false;
    apiFetch<ConfigStatusResponse>("/config/status")
      .then((data) => {
        if (!cancelled) setConfig(data);
      })
      .catch(() => {
        if (!cancelled) setConfig(null);
      })
      .finally(() => {
        if (!cancelled) setConfigLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Close the confirmation dialog with Escape.
  React.useEffect(() => {
    if (!deleteTarget) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDeleteTarget(null);
    };
    window.addEventListener("keydown", onKey);
    if (confirmRef.current) confirmRef.current.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [deleteTarget]);

  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [thread.length, pending]);

  function nowTime(): string {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  async function handleFile(file: File | null | undefined) {
    if (!file) return;
    setUploadError(null);
    const err = validateFile(file);
    if (err) {
      setUploadError(err);
      return;
    }
    setUploading(true);
    try {
      const created = await apiUpload<DocumentCreateResponse>("/documents", file);
      const row = toLibraryRow(created);
      setDocs((prev) => {
        const next = [row, ...prev.filter((d) => d.id !== row.id)];
        schedulePollIfNeeded(next);
        return next;
      });
    } catch (e) {
      setUploadError(
        e instanceof Error ? e.message : `“${file.name}” could not be uploaded. Please try again.`,
      );
    } finally {
      setUploading(false);
    }
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    handleFile(file);
  }

  function onDrop(e: React.DragEvent<HTMLElement>) {
    e.preventDefault();
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    handleFile(file);
  }

  function onDragOver(e: React.DragEvent<HTMLElement>) {
    e.preventDefault();
  }

  // Real retrieval + grounded generation: POST /documents/{id}/ask. The
  // question is appended to its document's own thread immediately, a loading
  // indicator shows while the request is in flight, and the real answer (or
  // the backend's readable error) lands in the same thread when it resolves.
  async function onSend(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const q = question.trim();
    if (!q || !selected || selected.status !== "ready" || pending) return;
    const docId = selected.id;
    const userMsg: Message = { id: `m_${Date.now()}_u`, role: "user", text: q, time: nowTime() };
    setThreads((prev) => ({ ...prev, [docId]: [...(prev[docId] || []), userMsg] }));
    setQuestion("");
    setPending({ docId });
    try {
      const res = await askDocument(docId, q);
      const reply: Message = {
        id: `m_${Date.now()}_a`,
        role: "assistant",
        text: res.answer,
        source: sourceLabel(res.source),
        time: nowTime(),
      };
      setThreads((prev) => ({ ...prev, [docId]: [...(prev[docId] || []), reply] }));
    } catch (err) {
      const notice: Message = {
        id: `m_${Date.now()}_e`,
        role: "notice",
        text: err instanceof Error ? err.message : "Could not get an answer. Please try again.",
        time: nowTime(),
      };
      setThreads((prev) => ({ ...prev, [docId]: [...(prev[docId] || []), notice] }));
    } finally {
      setPending(null);
    }
  }

  async function confirmDelete() {
    const target = deleteTarget;
    if (!target) return;
    setDeleteTarget(null);
    setDeleteError(null);
    try {
      await apiFetch<void>(`/documents/${target.id}`, { method: "DELETE" });
      setDocs((prev) => prev.filter((d) => d.id !== target.id));
      setThreads((prev) => {
        const next = { ...prev };
        delete next[target.id];
        return next;
      });
      if (selectedId === target.id) setSelectedId(null);
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : `Could not delete “${target.file_name}”.`);
    }
  }

  const readyCount = docs.filter((d) => d.status === "ready").length;

  return (
    <div
      className="min-h-full px-5 py-8 sm:px-8"
      style={{
        backgroundColor: brand.backgroundColor,
        fontFamily: brand.fontBody,
        color: "#3A342C",
      }}
    >
      <header className="mx-auto mb-8 max-w-6xl">
        <h1
          className="text-3xl font-semibold tracking-tight sm:text-4xl"
          style={{ fontFamily: brand.fontHeading, color: "#2A2622" }}
        >
          Reading Room
        </h1>
        <p className="mt-2 max-w-2xl text-base leading-relaxed" style={{ color: "#6B6256" }}>
          Ask questions of one document at a time. Every answer is drawn only from the document on
          the shelf you have selected, and carries its page or chunk reference.
        </p>
        <p className="mt-3 flex flex-wrap items-center gap-2 text-sm" style={{ color: "#6B6256" }}>
          {configLoading ? (
            <span>Checking answer model configuration…</span>
          ) : config ? (
            <>
              {config.llm_configured ? (
                <Icons.CheckCircle
                  className="h-4 w-4"
                  style={{ color: "#2F5D50" }}
                  aria-hidden="true"
                />
              ) : (
                <Icons.AlertCircle
                  className="h-4 w-4"
                  style={{ color: "#8A3524" }}
                  aria-hidden="true"
                />
              )}
              <span>
                {config.llm_configured
                  ? `Answer model configured — ${config.llm_model ?? "model"} · Embeddings ${
                      config.embedding_model ?? "model"
                    } · Index stored locally`
                  : "No answer model configured — uploading and search work, answers do not"}
              </span>
            </>
          ) : (
            <>
              <Icons.AlertCircle
                className="h-4 w-4"
                style={{ color: "#8A3524" }}
                aria-hidden="true"
              />
              <span>Could not reach the configuration service.</span>
            </>
          )}
        </p>
      </header>

      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[21rem_minmax(0,1fr)] lg:items-start">
        {/* ---------------- Shelf ---------------- */}
        <aside className="space-y-5">
          <section
            className="rounded-xl border p-5"
            style={{ borderColor: "#E2D7C4", backgroundColor: "#FFFDF8" }}
            onDrop={onDrop}
            onDragOver={onDragOver}
          >
            <h2 className="text-base font-semibold" style={{ fontFamily: brand.fontHeading }}>
              Add a document
            </h2>
            <p className="mt-1 text-sm leading-relaxed" style={{ color: "#6B6256" }}>
              PDF, DOCX or TXT, up to about 20 MB. Text is extracted, chunked and embedded on this
              machine.
            </p>

            <div className="mt-4">
              <Label htmlFor="file-upload" className="text-sm font-medium">
                Choose a file
              </Label>
              <input
                id="file-upload"
                ref={fileRef}
                type="file"
                accept=".pdf,.docx,.txt"
                onChange={onFileChange}
                disabled={uploading}
                className="mt-2 block w-full cursor-pointer rounded-lg border bg-white/70 p-2 text-sm file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-[#2F5D50] file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white disabled:cursor-not-allowed disabled:opacity-60"
                style={{ borderColor: "#DED2BD" }}
              />
              <p className="mt-2 text-xs" style={{ color: "#6B6256" }}>
                {uploading ? "Uploading…" : "You can also drop a file onto this panel."}
              </p>
            </div>

            {uploadError && (
              <div
                role="alert"
                className="mt-4 flex items-start gap-3 rounded-lg border p-3 text-sm"
                style={{ borderColor: "#E6BFB2", backgroundColor: "#F8E7E0", color: "#7C3020" }}
              >
                <Icons.AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <p className="flex-1 leading-relaxed">{uploadError}</p>
                <button
                  type="button"
                  onClick={() => setUploadError(null)}
                  aria-label="Dismiss upload error"
                  className="rounded p-0.5 focus-visible:ring-2 focus-visible:ring-offset-1"
                  style={{ color: "#7C3020" }}
                >
                  <Icons.X className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            )}
          </section>

          <section
            className="rounded-xl border p-5"
            style={{ borderColor: "#E2D7C4", backgroundColor: "#FFFDF8" }}
          >
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-base font-semibold" style={{ fontFamily: brand.fontHeading }}>
                Shelf
              </h2>
              <span className="text-xs" style={{ color: "#6B6256" }}>
                {docs.length} file{docs.length === 1 ? "" : "s"} · {readyCount} ready
              </span>
            </div>

            {docsLoading ? (
              <div
                className="mt-4 rounded-lg border border-dashed px-4 py-8 text-center"
                style={{ borderColor: "#DED2BD" }}
              >
                <p className="text-sm" style={{ color: "#6B6256" }}>
                  Loading your shelf…
                </p>
              </div>
            ) : docs.length === 0 ? (
              <div
                className="mt-4 rounded-lg border border-dashed px-4 py-8 text-center"
                style={{ borderColor: "#DED2BD" }}
              >
                <Icons.FileText
                  className="mx-auto h-6 w-6"
                  style={{ color: "#A2967F" }}
                  aria-hidden="true"
                />
                <p className="mt-3 text-sm font-medium">Your shelf is empty</p>
                <p className="mt-1 text-sm" style={{ color: "#6B6256" }}>
                  Upload a PDF, DOCX or TXT file above to start asking questions.
                </p>
              </div>
            ) : (
              <ul className="mt-4 space-y-2" aria-live="polite">
                {sortedDocs.map((doc) => {
                  const st = STATUS_STYLE[doc.status] || STATUS_STYLE.processing;
                  const isSelected = doc.id === selectedId;
                  const selectable = doc.status === "ready";
                  const chunkCount = doc.chunk_count ?? 0;
                  return (
                    <li key={doc.id}>
                      <div
                        className="rounded-lg border p-3 transition-colors"
                        style={{
                          borderColor: isSelected ? "#2F5D50" : "#E7DECE",
                          backgroundColor: isSelected ? "#EEF4F1" : "transparent",
                        }}
                      >
                        <div className="flex items-start gap-2">
                          <button
                            type="button"
                            onClick={() => selectable && setSelectedId(doc.id)}
                            disabled={!selectable}
                            aria-current={isSelected ? "true" : undefined}
                            className="flex-1 rounded text-left focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed"
                          >
                            <span className="block text-sm font-medium leading-snug break-words">
                              {doc.file_name}
                            </span>
                            <span className="mt-1 block text-xs" style={{ color: "#6B6256" }}>
                              {TYPE_LABEL[doc.file_type] || doc.file_type} · added{" "}
                              {formatDate(doc.created_at)}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(doc)}
                            aria-label={`Remove ${doc.file_name} from the shelf`}
                            className="rounded p-1.5 hover:bg-white focus-visible:ring-2 focus-visible:ring-offset-1"
                            style={{ color: "#6B6256" }}
                          >
                            <Icons.Trash className="h-4 w-4" aria-hidden="true" />
                          </button>
                        </div>

                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <span
                            className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
                            style={{ backgroundColor: st.bg, color: st.fg }}
                          >
                            {doc.status === "ready" && (
                              <Icons.CheckCircle className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            {doc.status === "failed" && (
                              <Icons.AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            {doc.status === "processing" && (
                              <span
                                className="h-3 w-3 animate-spin rounded-full border-2 border-current border-r-transparent"
                                aria-hidden="true"
                              />
                            )}
                            {st.label}
                          </span>
                          {doc.status === "ready" && (
                            <span className="text-xs" style={{ color: "#6B6256" }}>
                              {chunkCount} chunk{chunkCount === 1 ? "" : "s"} indexed
                            </span>
                          )}
                          {doc.status === "processing" && (
                            <span className="text-xs" style={{ color: "#6B6256" }}>
                              Processing…
                            </span>
                          )}
                        </div>

                        {doc.status === "failed" && (
                          <p className="mt-2 text-xs leading-relaxed" style={{ color: "#8A3524" }}>
                            {doc.error_message || "Processing failed."} Not available for questions.
                          </p>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            {docsError && (
              <p role="alert" className="mt-3 text-xs" style={{ color: "#8A3524" }}>
                {docsError}
              </p>
            )}
            {deleteError && (
              <p role="alert" className="mt-3 text-xs" style={{ color: "#8A3524" }}>
                {deleteError}
              </p>
            )}
          </section>
        </aside>

        {/* ---------------- Chat ---------------- */}
        <section
          className="flex min-h-[32rem] flex-col rounded-xl border"
          style={{ borderColor: "#E2D7C4", backgroundColor: "#FFFDF8" }}
        >
          <div
            className="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4"
            style={{ borderColor: "#EFE6D6" }}
          >
            <div className="min-w-0">
              <h2
                className="truncate text-lg font-semibold"
                style={{ fontFamily: brand.fontHeading }}
              >
                {selected ? selected.file_name : "No document selected"}
              </h2>
              <p className="mt-1 text-sm" style={{ color: "#6B6256" }}>
                {selected && selected.status === "ready"
                  ? `${TYPE_LABEL[selected.file_type] || selected.file_type} · answers cite ${
                      selected.file_type === "pdf" ? "page numbers" : "chunk numbers"
                    }`
                  : "Pick a ready document from the shelf to begin a thread."}
              </p>
            </div>
            {selected && (
              <div className="flex shrink-0 gap-2">
                <Btn
                  onClick={() => setThreads((prev) => ({ ...prev, [selected.id]: [] }))}
                  disabled={thread.length === 0}
                >
                  <Icons.Plus className="h-4 w-4" aria-hidden="true" />
                  New chat
                </Btn>
                <Btn onClick={() => setDeleteTarget(selected)}>
                  <Icons.Trash className="h-4 w-4" aria-hidden="true" />
                  Remove
                </Btn>
              </div>
            )}
          </div>

          <div
            ref={logRef}
            role="log"
            aria-live="polite"
            aria-label="Conversation"
            className="flex-1 space-y-4 overflow-y-auto px-5 py-5"
            style={{ maxHeight: "28rem" }}
          >
            {!selected && (
              <div className="flex h-full flex-col items-center justify-center py-12 text-center">
                <Icons.Package
                  className="h-7 w-7"
                  style={{ color: "#A2967F" }}
                  aria-hidden="true"
                />
                <h3 className="mt-3 text-base font-semibold">Nothing selected yet</h3>
                <p className="mt-1 max-w-sm text-sm" style={{ color: "#6B6256" }}>
                  Choose a document with a ready status from the shelf, or upload a new one.
                </p>
              </div>
            )}

            {selected && selected.status !== "ready" && (
              <div className="flex h-full flex-col items-center justify-center py-12 text-center">
                <Icons.AlertCircle
                  className="h-7 w-7"
                  style={{ color: "#A2967F" }}
                  aria-hidden="true"
                />
                <h3 className="mt-3 text-base font-semibold">Not ready for questions yet</h3>
                <p className="mt-1 max-w-sm text-sm" style={{ color: "#6B6256" }}>
                  Select a processed document first — this one is still {selected.status}.
                </p>
              </div>
            )}

            {selected && selected.status === "ready" && thread.length === 0 && (
              <div className="py-8 text-center">
                <Icons.FileText
                  className="mx-auto h-6 w-6"
                  style={{ color: "#A2967F" }}
                  aria-hidden="true"
                />
                <h3 className="mt-3 text-base font-semibold">This thread is empty</h3>
                <p className="mt-1 text-sm" style={{ color: "#6B6256" }}>
                  Ask anything about {selected.file_name}. Threads are kept for this session only.
                </p>
              </div>
            )}

            {selected &&
              selected.status === "ready" &&
              thread.map((m) =>
                m.role === "user" ? (
                  <div key={m.id} className="flex justify-end">
                    <div className="max-w-[85%]">
                      <p
                        className="rounded-xl px-4 py-2.5 text-sm leading-relaxed text-white"
                        style={{ backgroundColor: "#2F5D50" }}
                      >
                        {m.text}
                      </p>
                      <p className="mt-1 text-right text-xs" style={{ color: "#6B6256" }}>
                        You · {m.time}
                      </p>
                    </div>
                  </div>
                ) : m.role === "notice" ? (
                  <div
                    key={m.id}
                    role="status"
                    className="flex max-w-[92%] items-start gap-3 rounded-xl border px-4 py-3 text-sm leading-relaxed"
                    style={{ borderColor: "#E6BFB2", backgroundColor: "#F8E7E0", color: "#7C3020" }}
                  >
                    <Icons.AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    <div>
                      <p className="font-medium">Answer unavailable</p>
                      <p className="mt-1">{m.text}</p>
                    </div>
                  </div>
                ) : (
                  <div key={m.id} className="max-w-[92%]">
                    <div
                      className="rounded-xl border px-4 py-3"
                      style={{ borderColor: "#E7DECE", backgroundColor: "#FFFFFF" }}
                    >
                      <p className="text-sm leading-relaxed">{m.text}</p>
                      {m.source ? (
                        <p className="mt-3 flex flex-wrap items-center gap-2">
                          <span
                            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold"
                            style={{ backgroundColor: "#F6E3D6", color: "#8A4420" }}
                          >
                            <Icons.FileText className="h-3.5 w-3.5" aria-hidden="true" />
                            Source: {m.source}
                          </span>
                          <span className="text-xs" style={{ color: "#6B6256" }}>
                            {selected ? selected.file_name : ""}
                          </span>
                        </p>
                      ) : (
                        <p className="mt-3 text-xs" style={{ color: "#6B6256" }}>
                          No supporting passage was found, so no source is shown.
                        </p>
                      )}
                    </div>
                    <p className="mt-1 text-xs" style={{ color: "#6B6256" }}>
                      Reading Room · {m.time}
                    </p>
                  </div>
                ),
              )}

            {pending && pending.docId === selectedId && (
              <div
                role="status"
                className="flex items-center gap-3 text-sm"
                style={{ color: "#6B6256" }}
              >
                <span
                  className="h-4 w-4 animate-spin rounded-full border-2 border-r-transparent"
                  style={{ borderColor: "#2F5D50", borderRightColor: "transparent" }}
                  aria-hidden="true"
                />
                Retrieving the most relevant passages and composing an answer…
              </div>
            )}
          </div>

          <form onSubmit={onSend} className="border-t px-5 py-4" style={{ borderColor: "#EFE6D6" }}>
            <Label htmlFor="question" className="text-sm font-medium">
              Your question
            </Label>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <Input
                id="question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                disabled={!selected || selected.status !== "ready"}
                placeholder={
                  selected && selected.status === "ready"
                    ? "e.g. What does clause 4 say about notice?"
                    : "Select a processed document first"
                }
                aria-describedby="question-help"
                className="flex-1"
              />
              <Btn
                kind="primary"
                type="submit"
                disabled={!canAsk || question.trim() === ""}
                className="sm:w-auto"
              >
                <Icons.ArrowRight className="h-4 w-4" aria-hidden="true" />
                {pending ? "Sending…" : "Send"}
              </Btn>
            </div>
            <p
              id="question-help"
              className="mt-2 text-xs leading-relaxed"
              style={{ color: "#6B6256" }}
            >
              {selected && selected.status === "ready"
                ? "Answers come only from this document. If it isn't in there, you'll be told so rather than guessed at."
                : "Select a processed document first — sending is blocked until one with a ready status is chosen."}
            </p>
          </form>
        </section>
      </div>

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-title"
            aria-describedby="delete-desc"
            className="w-full max-w-md rounded-xl border p-6"
            style={{ borderColor: "#E2D7C4", backgroundColor: "#FFFDF8" }}
          >
            <h2
              id="delete-title"
              className="text-lg font-semibold"
              style={{ fontFamily: brand.fontHeading }}
            >
              Remove this document?
            </h2>
            <p
              id="delete-desc"
              className="mt-2 text-sm leading-relaxed"
              style={{ color: "#6B6256" }}
            >
              <span className="font-medium" style={{ color: "#3A342C" }}>
                {deleteTarget.file_name}
              </span>{" "}
              and all of its chunks and embeddings will be deleted from the local index. Its chat
              thread is discarded too. This cannot be undone.
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <Btn onClick={() => setDeleteTarget(null)}>Cancel</Btn>
              <Btn
                kind="primary"
                ref={confirmRef}
                onClick={confirmDelete}
                style={{ backgroundColor: "#8A3524" }}
              >
                Delete permanently
              </Btn>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
