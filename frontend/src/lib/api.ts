/// <reference types="vite/client" />
// Without the reference above, `import.meta.env` is not typed and `tsc --noEmit` fails --
// which `vite build` does not catch, because it tree-shakes this module out when no screen
// imports it yet.
//
// Where the generated API lives.
//
// Set at build time: the platform bakes the deployed API URL into the frontend build. The
// fallback is the local backend so a bare `npm run dev` still points somewhere real.
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/**
 * Typed document contract, mirrored from backend/app/schemas.py.
 *
 * `DocumentStatusResponse` is the shape of `GET /documents` (as a list) and
 * `GET /documents/{id}`. `DocumentCreateResponse` is the 202 body of
 * `POST /documents`: it deliberately has no `error_message`/`chunk_count`
 * because processing hasn't produced either yet.
 */
export type DocumentStatus = "ready" | "processing" | "failed";

export interface DocumentStatusResponse {
  id: string;
  file_name: string;
  file_type: string;
  status: DocumentStatus;
  error_message: string | null;
  chunk_count: number;
  created_at: string;
}

export interface DocumentCreateResponse {
  id: string;
  file_name: string;
  file_type: string;
  status: DocumentStatus;
  created_at: string;
}

export interface ConfigStatusResponse {
  llm_configured: boolean;
  embedding_configured: boolean;
  llm_model: string | null;
  embedding_model: string | null;
  issues: string[];
}

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
    if (body && typeof body.error === "string") return body.error;
  } catch {
    // Body wasn't JSON (or was empty) -- fall through to the generic message.
  }
  return fallback;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const message = await readErrorMessage(
      response,
      `${init?.method ?? "GET"} ${path} failed: ${response.status}`,
    );
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/**
 * Multipart upload path. `apiFetch` always sets a JSON Content-Type, which is
 * wrong for a file body (and would strip the multipart boundary the browser
 * needs to add itself), so uploads get their own fetch call instead of
 * reusing it.
 */
export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await readErrorMessage(response, `POST ${path} failed: ${response.status}`);
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/** Typed call surface for the document library, per contract:
 * GET /documents -> DocumentStatusResponse[]
 * POST /documents -> 202 DocumentCreateResponse
 * GET /documents/{id} -> DocumentStatusResponse
 * DELETE /documents/{id} -> 204
 */
export function fetchDocuments(): Promise<DocumentStatusResponse[]> {
  return apiFetch<DocumentStatusResponse[]>("/documents");
}

export function fetchDocument(id: string): Promise<DocumentStatusResponse> {
  return apiFetch<DocumentStatusResponse>(`/documents/${id}`);
}

export function uploadDocument(file: File): Promise<DocumentCreateResponse> {
  return apiUpload<DocumentCreateResponse>("/documents", file);
}

export function deleteDocument(id: string): Promise<void> {
  return apiFetch<void>(`/documents/${id}`, { method: "DELETE" });
}

export function fetchConfigStatus(): Promise<ConfigStatusResponse> {
  return apiFetch<ConfigStatusResponse>("/config/status");
}
