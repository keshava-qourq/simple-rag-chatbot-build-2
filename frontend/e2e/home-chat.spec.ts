import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * Covers asking, document switching and new-chat on the Home screen
 * (US-011-2). Network is fully mocked per the packet's contract, following
 * the conventions in documents.spec.ts / document-status.spec.ts: GET
 * /documents, GET /config/status and POST /documents/{id}/ask are all
 * intercepted so the suite runs without a live backend.
 */

const CONFIG_RESPONSE = {
  llm_configured: true,
  embedding_configured: true,
  llm_model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  issues: [],
};

type AskHandler = (
  docId: string,
  question: string,
) => Promise<{ status: number; body: unknown }> | { status: number; body: unknown };

interface MockOptions {
  initialDocs?: unknown[];
  onAsk?: AskHandler;
  /** Artificial delay (ms) before the ask route responds, so a loading
   * indicator can be observed mid-flight. */
  askDelayMs?: number;
}

async function mockBackend(page: Page, options: MockOptions = {}): Promise<void> {
  const docs = options.initialDocs ? [...options.initialDocs] : [];

  await page.route("**/config/status", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CONFIG_RESPONSE),
    });
  });

  await page.route("**/documents", async (route: Route) => {
    const request = route.request();
    if (request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(docs),
      });
      return;
    }
    await route.continue();
  });

  await page.route("**/documents/*/ask", async (route: Route) => {
    const request = route.request();
    const match = request.url().match(/documents\/([^/]+)\/ask/);
    const docId = match ? match[1] : "";
    const payload = JSON.parse(request.postData() || "{}");
    const question = payload.question ?? "";

    if (options.askDelayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.askDelayMs));
    }

    if (options.onAsk) {
      const result = await options.onAsk(docId, question);
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        answer: `Answer for ${docId}: ${question}`,
        source: { document_name: "doc", page_number: 3, chunk_index: null },
        is_fallback: false,
        model_configured: true,
      }),
    });
  });
}

const READY_DOC_A = {
  id: "doc-a",
  file_name: "First.pdf",
  file_type: "pdf",
  status: "ready",
  error_message: null,
  chunk_count: 4,
  created_at: "2026-10-01T00:00:00Z",
};

const READY_DOC_B = {
  id: "doc-b",
  file_name: "Second.txt",
  file_type: "txt",
  status: "ready",
  error_message: null,
  chunk_count: 2,
  created_at: "2026-10-02T00:00:00Z",
};

// AC-031: ready document selected, question typed and sent — question
// appears, a loading indicator shows while the request is in flight, and the
// real answer lands in the same thread.
test("AC-031: asking a ready document shows the question, a loading indicator, then the answer in the same thread", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [READY_DOC_A], askDelayMs: 300 });
  await page.goto("/");

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  const input = page.getByLabel("Your question");
  await expect(input).toBeEnabled();

  await input.fill("What does clause 4 say?");
  await page.getByRole("button", { name: /Send/ }).click();

  await expect(page.getByText("What does clause 4 say?")).toBeVisible();
  await expect(page.getByText(/Retrieving the most relevant passages/)).toBeVisible();

  await expect(page.getByText(/Answer for doc-a: What does clause 4 say\?/)).toBeVisible({
    timeout: 6000,
  });
  await expect(page.getByText(/Retrieving the most relevant passages/)).toHaveCount(0);
});

// AC-032: with no document selected (or only a non-ready one present to
// select from), the question box stays disabled and shows the
// select-a-processed-document message.
test("AC-032: no selection leaves the question box disabled with the select-a-document message", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");

  const input = page.getByLabel("Your question");
  await expect(input).toBeDisabled();
  await expect(
    page.getByText(
      "Select a processed document first — sending is blocked until one with a ready status is chosen.",
    ),
  ).toBeVisible();
});

test("AC-032: only a non-ready document on the shelf also leaves the question box disabled", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
      {
        id: "doc-c",
        file_name: "Draft.txt",
        file_type: "txt",
        status: "processing",
        error_message: null,
        chunk_count: 0,
        created_at: "2026-10-01T00:00:00Z",
      },
    ],
  });
  await page.goto("/");

  await expect(page.getByText("Draft.txt")).toBeVisible();
  const input = page.getByLabel("Your question");
  await expect(input).toBeDisabled();
  await expect(
    page.getByText(
      "Select a processed document first — sending is blocked until one with a ready status is chosen.",
    ),
  ).toBeVisible();
});

// AC-033: a whitespace-only question adds no message to the thread.
test("AC-033: a whitespace-only question adds no message", async ({ page }) => {
  await mockBackend(page, { initialDocs: [READY_DOC_A] });
  await page.goto("/");

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  const input = page.getByLabel("Your question");
  await input.fill("   ");

  await expect(page.getByRole("button", { name: /Send/ })).toBeDisabled();
  await expect(page.getByText("This thread is empty")).toBeVisible();
});

// AC-021/AC-022: switching A -> B -> A keeps each document's thread separate,
// with no cross-contamination of questions or answers.
test("AC-021/AC-022: switching between two documents and back preserves each thread", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [READY_DOC_A, READY_DOC_B],
    onAsk: (docId) => ({
      status: 200,
      body: {
        answer: docId === "doc-a" ? "Answer for A" : "Answer for B",
        source: null,
        is_fallback: false,
        model_configured: true,
      },
    }),
  });
  await page.goto("/");

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  let input = page.getByLabel("Your question");
  await input.fill("Question for A");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText("Answer for A")).toBeVisible();

  await page.getByRole("button", { name: /Second\.txt/ }).click();
  await expect(page.getByText("Question for A")).toHaveCount(0);
  await expect(page.getByText("This thread is empty")).toBeVisible();

  input = page.getByLabel("Your question");
  await input.fill("Question for B");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText("Answer for B")).toBeVisible();

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  await expect(page.getByText("Question for A")).toBeVisible();
  await expect(page.getByText("Answer for A")).toBeVisible();
  await expect(page.getByText("Question for B")).toHaveCount(0);
  await expect(page.getByText("Answer for B")).toHaveCount(0);
});

// AC-023: processing and failed documents are not selectable, and show their
// status/reason on the shelf.
test("AC-023: processing and failed documents are not selectable and show their status/reason", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
      {
        id: "doc-proc",
        file_name: "Processing.pdf",
        file_type: "pdf",
        status: "processing",
        error_message: null,
        chunk_count: 0,
        created_at: "2026-10-01T00:00:00Z",
      },
      {
        id: "doc-fail",
        file_name: "Failed.pdf",
        file_type: "pdf",
        status: "failed",
        error_message: "Could not extract text: the PDF is encrypted.",
        chunk_count: 0,
        created_at: "2026-10-02T00:00:00Z",
      },
    ],
  });
  await page.goto("/");

  await expect(page.getByText("Processing.pdf")).toBeVisible();
  await expect(page.getByText("Processing…")).toBeVisible();
  await expect(page.getByText("Failed.pdf")).toBeVisible();
  await expect(page.getByText("Could not extract text: the PDF is encrypted.")).toBeVisible();

  const processingBtn = page.getByRole("button", { name: /Processing\.pdf/ });
  const failedBtn = page.getByRole("button", { name: /Failed\.pdf/ });
  await expect(processingBtn).toBeDisabled();
  await expect(failedBtn).toBeDisabled();

  // Clicking does not select either (disabled, but belt-and-braces: no
  // aria-current appears and the question box stays disabled).
  await processingBtn.click({ force: true });
  await failedBtn.click({ force: true });
  await expect(processingBtn).not.toHaveAttribute("aria-current", "true");
  await expect(failedBtn).not.toHaveAttribute("aria-current", "true");
  await expect(page.getByLabel("Your question")).toBeDisabled();
});

// AC-029/AC-030: New chat empties only the selected document's thread; the
// document stays in the library and selected, and another document's thread
// is unaffected.
test("AC-029/AC-030: New chat clears only the selected thread, keeps the document selected, and leaves document B untouched", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [READY_DOC_A, READY_DOC_B],
    onAsk: (docId) => ({
      status: 200,
      body: {
        answer: docId === "doc-a" ? "Answer for A" : "Answer for B",
        source: null,
        is_fallback: false,
        model_configured: true,
      },
    }),
  });
  await page.goto("/");

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  let input = page.getByLabel("Your question");
  await input.fill("Question for A");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText("Answer for A")).toBeVisible();

  await page.getByRole("button", { name: /Second\.txt/ }).click();
  input = page.getByLabel("Your question");
  await input.fill("Question for B");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText("Answer for B")).toBeVisible();

  const firstBtn = page.getByRole("button", { name: /First\.pdf/ });
  await firstBtn.click();
  await expect(page.getByText("Question for A")).toBeVisible();

  await page.getByRole("button", { name: "New chat" }).click();

  await expect(page.getByText("Question for A")).toHaveCount(0);
  await expect(page.getByText("This thread is empty")).toBeVisible();
  // The document stays in the library and remains selected.
  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(firstBtn).toHaveAttribute("aria-current", "true");

  // Document B's thread is unaffected.
  await page.getByRole("button", { name: /Second\.txt/ }).click();
  await expect(page.getByText("Question for B")).toBeVisible();
  await expect(page.getByText("Answer for B")).toBeVisible();
});
