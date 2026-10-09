import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, apiUpload } from "@/lib/api";

import Home from "./Home";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiUpload: vi.fn(),
}));

const mockedApiFetch = vi.mocked(apiFetch);
const mockedApiUpload = vi.mocked(apiUpload);

const CONFIG_OK = {
  llm_configured: true,
  embedding_configured: true,
  llm_model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  issues: [],
};

const CONFIG_MISSING = {
  llm_configured: false,
  embedding_configured: false,
  llm_model: null,
  embedding_model: null,
  issues: ["LLM_API_KEY is not set"],
};

function setupFetch({
  docs = [],
  config = CONFIG_OK,
}: {
  docs?: unknown[];
  config?: unknown;
} = {}) {
  mockedApiFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/documents" && (!init || init.method === undefined)) {
      return docs as never;
    }
    if (path === "/config/status") {
      return config as never;
    }
    if (path.startsWith("/documents/") && init?.method === "DELETE") {
      return undefined as never;
    }
    throw new Error(`unexpected apiFetch call: ${path}`);
  });
}

beforeEach(() => {
  mockedApiFetch.mockReset();
  mockedApiUpload.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Home screen", () => {
  it("shows the empty-shelf state, not an error, when no documents exist", async () => {
    setupFetch({ docs: [] });
    render(<Home />);

    expect(await screen.findByText("Your shelf is empty")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders uploaded documents and reflects the config banner from the backend", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "Report.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 12,
          created_at: "2026-10-03T00:00:00Z",
        },
      ],
      config: CONFIG_MISSING,
    });
    render(<Home />);

    expect(await screen.findByText("Report.pdf")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "No answer model configured — uploading and search work, answers do not",
      ),
    ).toBeInTheDocument();
  });

  it("rejects an unsupported file type client-side without calling the API", async () => {
    setupFetch({ docs: [] });
    render(<Home />);
    await screen.findByText("Your shelf is empty");

    const input = screen.getByLabelText("Choose a file") as HTMLInputElement;
    const file = new File(["data"], "sheet.xlsx", {
      type: "application/vnd.ms-excel",
    });
    fireEvent.change(input, { target: { files: [file] } });

    expect(
      await screen.findByText(
        /is not a supported file type\. Reading Room accepts PDF, DOCX and TXT files only\./,
      ),
    ).toBeInTheDocument();
    expect(mockedApiUpload).not.toHaveBeenCalled();
  });

  it("uploads a supported file and shows it with a processing status immediately", async () => {
    setupFetch({ docs: [] });
    mockedApiUpload.mockResolvedValue({
      id: "doc-new",
      file_name: "notes.txt",
      file_type: "txt",
      status: "processing",
      created_at: "2026-10-09T00:00:00Z",
    } as never);
    render(<Home />);
    await screen.findByText("Your shelf is empty");

    const input = screen.getByLabelText("Choose a file") as HTMLInputElement;
    const file = new File(["hello world"], "notes.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
    expect(mockedApiUpload).toHaveBeenCalledWith("/documents", file);
    expect(screen.getByText("Processing")).toBeInTheDocument();
  });

  it("deletes a document after confirmation", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "Report.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 12,
          created_at: "2026-10-03T00:00:00Z",
        },
      ],
    });
    render(<Home />);
    await screen.findByText("Report.pdf");

    fireEvent.click(screen.getByLabelText("Remove Report.pdf from the shelf"));
    expect(await screen.findByText("Remove this document?")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Delete permanently"));

    await waitFor(() =>
      expect(mockedApiFetch).toHaveBeenCalledWith("/documents/doc-1", { method: "DELETE" }),
    );
    await waitFor(() => expect(screen.queryByText("Report.pdf")).not.toBeInTheDocument());
  });

  it("shows a failed document's error message and does not offer it for questions", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-2",
          file_name: "Scanned.pdf",
          file_type: "pdf",
          status: "failed",
          error_message: "No text could be extracted.",
          chunk_count: 0,
          created_at: "2026-10-03T00:00:00Z",
        },
      ],
    });
    render(<Home />);

    expect(await screen.findByText(/No text could be extracted\./)).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});
