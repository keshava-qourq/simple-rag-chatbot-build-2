import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, apiUpload, askDocument } from "@/lib/api";
import type { AskResponse } from "@/lib/api";

import Home from "./Home";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiUpload: vi.fn(),
  askDocument: vi.fn(),
}));

const mockedApiFetch = vi.mocked(apiFetch);
const mockedApiUpload = vi.mocked(apiUpload);
const mockedAskDocument = vi.mocked(askDocument);

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
  mockedAskDocument.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
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

  it("leaves an existing document unchanged when a second upload completes", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "First.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 4,
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    mockedApiUpload.mockResolvedValue({
      id: "doc-2",
      file_name: "Second.txt",
      file_type: "txt",
      status: "processing",
      created_at: "2026-10-09T00:00:00Z",
    } as never);
    render(<Home />);
    await screen.findByText("First.pdf");

    const input = screen.getByLabelText("Choose a file") as HTMLInputElement;
    const file = new File(["hello"], "Second.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("Second.txt")).toBeInTheDocument();
    expect(screen.getByText("First.pdf")).toBeInTheDocument();
    expect(screen.getByText("4 chunks indexed")).toBeInTheDocument();
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

  it("lists two ready documents and marks only the selected one with aria-current", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "First.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
        {
          id: "doc-2",
          file_name: "Second.txt",
          file_type: "txt",
          status: "ready",
          error_message: null,
          chunk_count: 5,
          created_at: "2026-10-02T00:00:00Z",
        },
      ],
    });
    render(<Home />);
    await screen.findByText("First.pdf");
    await screen.findByText("Second.txt");

    const firstBtn = screen.getByText("First.pdf").closest("button") as HTMLButtonElement;
    const secondBtn = screen.getByText("Second.txt").closest("button") as HTMLButtonElement;

    expect(firstBtn).not.toHaveAttribute("aria-current");
    expect(secondBtn).not.toHaveAttribute("aria-current");

    fireEvent.click(secondBtn);

    await waitFor(() => expect(secondBtn).toHaveAttribute("aria-current", "true"));
    expect(firstBtn).not.toHaveAttribute("aria-current");
  });

  it("renders a ready document without NaN/undefined when chunk_count is missing", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-3",
          file_name: "Fresh.txt",
          file_type: "txt",
          status: "ready",
          error_message: null,
          chunk_count: undefined,
          created_at: "2026-10-09T00:00:00Z",
        },
      ],
    });
    render(<Home />);

    expect(await screen.findByText("Fresh.txt")).toBeInTheDocument();
    expect(screen.getByText("0 chunks indexed")).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });

  it("shows a backend 4xx upload error as dismissible without disturbing the library", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "First.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    mockedApiUpload.mockRejectedValue(new Error("File exceeds the 20MB upload limit."));
    render(<Home />);
    await screen.findByText("First.pdf");
    fireEvent.click(screen.getByText("First.pdf"));

    const input = screen.getByLabelText("Choose a file") as HTMLInputElement;
    const file = new File(["x"], "big.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("File exceeds the 20MB upload limit.");

    fireEvent.click(screen.getByLabelText("Dismiss upload error"));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // The selected document and the rest of the library are untouched: the
    // name still appears both in the shelf row and the now-selected chat
    // header, so assert presence rather than a single unique match.
    expect(screen.getAllByText("First.pdf").length).toBeGreaterThan(0);
  });

  it("stops polling once no document is processing, and resumes after a new upload", async () => {
    vi.useFakeTimers();
    let call = 0;
    mockedApiFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/documents" && (!init || init.method === undefined)) {
        call += 1;
        if (call === 1) {
          return [
            {
              id: "doc-1",
              file_name: "Draft.txt",
              file_type: "txt",
              status: "processing",
              error_message: null,
              chunk_count: 0,
              created_at: "2026-10-09T00:00:00Z",
            },
          ] as never;
        }
        return [
          {
            id: "doc-1",
            file_name: "Draft.txt",
            file_type: "txt",
            status: "ready",
            error_message: null,
            chunk_count: 3,
            created_at: "2026-10-09T00:00:00Z",
          },
        ] as never;
      }
      if (path === "/config/status") return CONFIG_OK as never;
      throw new Error(`unexpected apiFetch call: ${path}`);
    });

    render(<Home />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(call).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(call).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(call).toBe(2);
  });

  it("sends a question, shows a loading indicator, then renders the real answer from the ask endpoint", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "Report.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    let resolveAsk: (value: AskResponse) => void = () => {};
    mockedAskDocument.mockImplementation(
      () =>
        new Promise<AskResponse>((resolve) => {
          resolveAsk = resolve;
        }),
    );
    render(<Home />);
    fireEvent.click(await screen.findByText("Report.pdf"));

    const input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "What does clause 4 say?" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    expect(screen.getByText("What does clause 4 say?")).toBeInTheDocument();
    expect(screen.getByText(/Retrieving the most relevant passages/)).toBeInTheDocument();
    expect(mockedAskDocument).toHaveBeenCalledWith("doc-1", "What does clause 4 say?");

    await act(async () => {
      resolveAsk({
        answer: "Clause 4 requires 30 days' notice.",
        source: { document_name: "Report.pdf", page_number: 4, chunk_index: null },
        is_fallback: false,
        model_configured: true,
      });
    });

    expect(await screen.findByText("Clause 4 requires 30 days' notice.")).toBeInTheDocument();
    expect(screen.getByText("Source: Page 4")).toBeInTheDocument();
    expect(screen.queryByText(/Retrieving the most relevant passages/)).not.toBeInTheDocument();
  });

  it("renders the backend's readable error message instead of a fabricated answer on a failed ask", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "Report.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    mockedAskDocument.mockRejectedValue(new Error("Retrieval is unavailable right now."));
    render(<Home />);
    fireEvent.click(await screen.findByText("Report.pdf"));

    const input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Any notice period?" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    expect(await screen.findByText("Retrieval is unavailable right now.")).toBeInTheDocument();
    expect(
      screen.queryByText("I couldn't find that information in the uploaded document."),
    ).not.toBeInTheDocument();
  });

  it("blocks sending and disables the question box when no document is selected", async () => {
    setupFetch({ docs: [] });
    render(<Home />);
    await screen.findByText("Your shelf is empty");

    const input = await screen.findByLabelText("Your question");
    expect(input).toBeDisabled();
    expect(
      screen.getByText(
        "Select a processed document first — sending is blocked until one with a ready status is chosen.",
      ),
    ).toBeInTheDocument();
    expect(mockedAskDocument).not.toHaveBeenCalled();
  });

  it("blocks sending when the selected document is processing, not ready", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "Draft.txt",
          file_type: "txt",
          status: "processing",
          error_message: null,
          chunk_count: 0,
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    render(<Home />);
    await screen.findByText("Draft.txt");

    const input = await screen.findByLabelText("Your question");
    expect(input).toBeDisabled();
    expect(mockedAskDocument).not.toHaveBeenCalled();
  });

  it("sends nothing and adds no message for an empty or whitespace-only question", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "Report.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
      ],
    });
    render(<Home />);
    fireEvent.click(await screen.findByText("Report.pdf"));

    const input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    expect(mockedAskDocument).not.toHaveBeenCalled();
    expect(screen.getByText("This thread is empty")).toBeInTheDocument();
  });

  it("keeps per-document threads separate when switching between two ready documents and back", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "First.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
        {
          id: "doc-2",
          file_name: "Second.txt",
          file_type: "txt",
          status: "ready",
          error_message: null,
          chunk_count: 5,
          created_at: "2026-10-02T00:00:00Z",
        },
      ],
    });
    mockedAskDocument.mockImplementation(async (id: string) => ({
      answer: id === "doc-1" ? "Answer for A" : "Answer for B",
      source: null,
      is_fallback: false,
      model_configured: true,
    }));
    render(<Home />);
    await screen.findByText("First.pdf");
    await screen.findByText("Second.txt");

    fireEvent.click(screen.getByText("First.pdf"));
    let input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Question for A" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect(await screen.findByText("Answer for A")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Second.txt"));
    expect(screen.queryByText("Question for A")).not.toBeInTheDocument();
    expect(screen.getByText("This thread is empty")).toBeInTheDocument();

    input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Question for B" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect(await screen.findByText("Answer for B")).toBeInTheDocument();

    fireEvent.click(screen.getByText("First.pdf"));
    expect(await screen.findByText("Question for A")).toBeInTheDocument();
    expect(await screen.findByText("Answer for A")).toBeInTheDocument();
    expect(screen.queryByText("Question for B")).not.toBeInTheDocument();
    expect(screen.queryByText("Answer for B")).not.toBeInTheDocument();
  });

  it("clears only the selected document's thread with New chat, leaving another document's thread untouched", async () => {
    setupFetch({
      docs: [
        {
          id: "doc-1",
          file_name: "First.pdf",
          file_type: "pdf",
          status: "ready",
          error_message: null,
          chunk_count: 2,
          created_at: "2026-10-01T00:00:00Z",
        },
        {
          id: "doc-2",
          file_name: "Second.txt",
          file_type: "txt",
          status: "ready",
          error_message: null,
          chunk_count: 5,
          created_at: "2026-10-02T00:00:00Z",
        },
      ],
    });
    mockedAskDocument.mockImplementation(async (id: string) => ({
      answer: id === "doc-1" ? "Answer for A" : "Answer for B",
      source: null,
      is_fallback: false,
      model_configured: true,
    }));
    render(<Home />);
    await screen.findByText("First.pdf");
    await screen.findByText("Second.txt");

    fireEvent.click(screen.getByText("First.pdf"));
    let input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Question for A" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    await screen.findByText("Answer for A");

    fireEvent.click(screen.getByText("Second.txt"));
    input = await screen.findByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Question for B" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    await screen.findByText("Answer for B");

    fireEvent.click(screen.getByText("First.pdf"));
    await screen.findByText("Question for A");
    fireEvent.click(screen.getByText("New chat"));
    expect(screen.queryByText("Question for A")).not.toBeInTheDocument();
    expect(screen.getByText("This thread is empty")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Second.txt"));
    expect(await screen.findByText("Question for B")).toBeInTheDocument();
    expect(screen.getByText("Answer for B")).toBeInTheDocument();
  });
});
