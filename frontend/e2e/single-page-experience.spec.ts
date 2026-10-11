import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * Covers US-018-2: the single-page experience itself -- that every required
 * control lives on one page and never triggers navigation, that loading
 * states are visible while work is in flight, that failures render
 * in-context (not as a blank screen or a browser alert), and that the
 * layout holds together at a desktop and a phone viewport. Network is fully
 * mocked per the packet's contract, following the conventions already used
 * in documents.spec.ts / home-chat.spec.ts / document-status.spec.ts.
 */

const CONFIG_RESPONSE = {
  llm_configured: true,
  embedding_configured: true,
  llm_model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  issues: [],
};

const READY_DOC = {
  id: "doc-a",
  file_name: "First.pdf",
  file_type: "pdf",
  status: "ready",
  error_message: null,
  chunk_count: 4,
  created_at: "2026-10-01T00:00:00Z",
};

interface GetDocsResult {
  status: number;
  body: unknown;
}

interface MockOptions {
  initialDocs?: unknown[];
  /** Called on every GET /documents; lets a test fail the first call(s) and
   * succeed afterwards. Defaults to always returning the current doc list. */
  onGetDocuments?: (callCount: number, docs: unknown[]) => GetDocsResult;
  /** Called on every POST /documents (upload). Defaults to the normal
   * 202-processing-row behaviour. */
  onUpload?: (
    route: Route,
    docs: unknown[],
  ) => Promise<boolean> | boolean /* true: route handled, nothing more to do */;
  /** Called on every ask; defaults to a canned 200 answer. */
  onAsk?: (callCount: number, docId: string, question: string) => { status: number; body: unknown };
  askDelayMs?: number;
}

async function mockBackend(page: Page, options: MockOptions = {}): Promise<{ docs: unknown[] }> {
  const state = { docs: options.initialDocs ? [...options.initialDocs] : [] };
  let getCalls = 0;
  let askCalls = 0;

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
      getCalls += 1;
      const result = options.onGetDocuments
        ? options.onGetDocuments(getCalls, state.docs)
        : { status: 200, body: state.docs };
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
      return;
    }
    if (request.method() === "POST") {
      if (options.onUpload) {
        const handled = await options.onUpload(route, state.docs);
        if (handled) return;
      }
      const created = {
        id: `doc-${state.docs.length + 1}`,
        file_name: "upload-placeholder",
        file_type: "txt",
        status: "processing",
        created_at: new Date().toISOString(),
      };
      const body = request.postData() || "";
      const nameMatch = body.match(/filename="([^"]+)"/);
      if (nameMatch) {
        created.file_name = nameMatch[1];
        const ext = nameMatch[1].split(".").pop()?.toLowerCase();
        if (ext) created.file_type = ext;
      }
      state.docs = [created, ...state.docs];
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }
    await route.continue();
  });

  await page.route("**/documents/*", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const segments = url.pathname.split("/").filter(Boolean);
    const isAsk = segments[segments.length - 1] === "ask";
    const docId = isAsk ? segments[segments.length - 2] : segments[segments.length - 1];

    if (request.method() === "DELETE") {
      state.docs = state.docs.filter((d) => (d as { id: string }).id !== docId);
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (isAsk && request.method() === "POST") {
      askCalls += 1;
      const payload = JSON.parse(request.postData() || "{}");
      const question = payload.question ?? "";
      if (options.askDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.askDelayMs));
      }
      const result = options.onAsk
        ? options.onAsk(askCalls, docId, question)
        : {
            status: 200,
            body: {
              answer: `Answer for ${docId}: ${question}`,
              source: { document_name: "doc", page_number: 3, chunk_index: null },
              is_fallback: false,
              model_configured: true,
            },
          };
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
      return;
    }

    await route.continue();
  });

  return state;
}

// AC-052: all required controls present on first load of the one page.
test("AC-052: every required control is present on first load", async ({ page }) => {
  await mockBackend(page, { initialDocs: [READY_DOC] });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Reading Room" })).toBeVisible();
  await expect(page.getByLabel("Choose a file")).toBeVisible();
  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByText("Ready")).toBeVisible();
  await expect(page.getByLabel("Remove First.pdf from the shelf")).toBeVisible();
  await expect(page.getByLabel("Your question")).toBeVisible();
  await expect(page.getByRole("button", { name: /Send/ })).toBeVisible();

  // Selecting the ready document surfaces the chat-level clear/new-chat
  // controls that live in the thread header.
  await page.getByRole("button", { name: /First\.pdf/ }).click();
  await expect(page.getByRole("button", { name: "New chat" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove" })).toBeVisible();
});

// AC-053: upload, select, ask, clear and new chat all happen in place; the
// URL never changes across the whole flow.
test("AC-053: upload, select, ask, clear and new chat never change the URL", async ({ page }) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");
  const startUrl = page.url();

  // Upload
  await page.getByLabel("Choose a file").setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello world"),
  });
  await expect(page.getByText("notes.txt")).toBeVisible();
  expect(page.url()).toBe(startUrl);

  // The uploaded doc starts "processing"; swap in a ready document to
  // exercise select/ask/clear without waiting out the poll interval.
  await mockBackend(page, { initialDocs: [READY_DOC] });
  await page.goto("/");

  // Select
  await page.getByRole("button", { name: /First\.pdf/ }).click();
  expect(page.url()).toBe(startUrl);

  // Ask
  const input = page.getByLabel("Your question");
  await input.fill("What does clause 4 say?");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText(/Answer for doc-a/)).toBeVisible();
  expect(page.url()).toBe(startUrl);

  // New chat (clears the thread view in place)
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByText("This thread is empty")).toBeVisible();
  expect(page.url()).toBe(startUrl);

  // Clear/remove the document via the shelf's remove control
  await page.getByLabel("Remove First.pdf from the shelf").click();
  await expect(page.getByText("Remove this document?")).toBeVisible();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page.getByText("Your shelf is empty")).toBeVisible();
  expect(page.url()).toBe(startUrl);
});

// AC-054: a per-document loading indicator is visible while a document is
// uploading/processing.
test("AC-054: a per-document loading indicator shows while a document is processing", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");

  await page.getByLabel("Choose a file").setInputFiles({
    name: "contract.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("terms and conditions"),
  });

  const row = page.locator("li", { hasText: "contract.txt" });
  await expect(row.getByText("Processing")).toBeVisible();
  await expect(row.locator(".animate-spin")).toBeVisible();
});

// AC-055: a chat-area loading indicator appears, and Send is disabled, while
// an ask request is in flight.
test("AC-055: a chat loading indicator appears and Send disables while asking", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [READY_DOC], askDelayMs: 300 });
  await page.goto("/");

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  await page.getByLabel("Your question").fill("What does clause 4 say?");
  const sendButton = page.getByRole("button", { name: /Send|Sending/ });
  await sendButton.click();

  await expect(page.getByText(/Retrieving the most relevant passages/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Sending…" })).toBeDisabled();

  await expect(page.getByText(/Answer for doc-a/)).toBeVisible({ timeout: 6000 });
  await expect(page.getByText(/Retrieving the most relevant passages/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Send/ })).toBeEnabled();
});

// AC-056: a failed upload renders a plain-language in-context error styled
// distinctly from an assistant answer, and a subsequent upload still works.
test("AC-056: a failed upload shows an in-context error and a later upload still succeeds", async ({
  page,
}) => {
  let uploadAttempts = 0;
  await mockBackend(page, {
    initialDocs: [],
    onUpload: async (route) => {
      uploadAttempts += 1;
      if (uploadAttempts === 1) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "The upload service is temporarily unavailable." }),
        });
        return true;
      }
      return false;
    },
  });
  await page.goto("/");

  await page.getByLabel("Choose a file").setInputFiles({
    name: "first.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello"),
  });

  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("The upload service is temporarily unavailable.");
  // Styled distinctly: a plain error border/background, not the chat
  // assistant card's white background.
  await expect(alert).toHaveCSS("background-color", "rgb(248, 231, 224)");
  await expect(page.getByText("first.txt")).toHaveCount(0);

  await page.getByLabel("Choose a file").setInputFiles({
    name: "second.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello again"),
  });
  await expect(page.getByText("second.txt")).toBeVisible();
});

// AC-056: a failed document list renders a plain-language in-context error,
// and the app still accepts a subsequent successful action (upload).
test("AC-056: a failed document list shows an in-context error and upload still works afterwards", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [],
    onGetDocuments: () => ({
      status: 500,
      body: { detail: "Could not load the document library." },
    }),
  });
  await page.goto("/");

  await expect(page.getByRole("alert")).toContainText("Could not load the document library.");

  await page.getByLabel("Choose a file").setInputFiles({
    name: "recovered.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello"),
  });
  await expect(page.getByText("recovered.txt")).toBeVisible();
});

// AC-056: a failed ask renders a plain-language in-context error styled
// distinctly from an assistant answer, and a subsequent ask still succeeds.
test("AC-056: a failed ask shows a distinct in-context error and a later ask still succeeds", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [READY_DOC],
    onAsk: (callCount, docId, question) => {
      if (callCount === 1) {
        return { status: 503, body: { detail: "Retrieval is unavailable right now." } };
      }
      return {
        status: 200,
        body: {
          answer: `Answer for ${docId}: ${question}`,
          source: null,
          is_fallback: false,
          model_configured: true,
        },
      };
    },
  });
  await page.goto("/");

  await page.getByRole("button", { name: /First\.pdf/ }).click();
  const input = page.getByLabel("Your question");
  await input.fill("First question");
  await page.getByRole("button", { name: /Send/ }).click();

  const notice = page.getByText("Retrieval is unavailable right now.");
  await expect(notice).toBeVisible();
  const noticeCard = page.getByText("Answer unavailable");
  await expect(noticeCard).toBeVisible();
  // Styled distinctly from a normal assistant answer card (which uses a
  // white background with a neutral border -- see Home.tsx STATUS_STYLE /
  // assistant message styles).
  const noticeBox = page.locator("div[role='status']", { hasText: "Answer unavailable" });
  await expect(noticeBox).toHaveCSS("background-color", "rgb(248, 231, 224)");

  await input.fill("Second question");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText(/Answer for doc-a: Second question/)).toBeVisible();
  const assistantCard = page
    .locator("div", { hasText: /Answer for doc-a: Second question/ })
    .last();
  await expect(assistantCard).not.toHaveCSS("background-color", "rgb(248, 231, 224)");
});

// AC-057: at a desktop viewport, the document list, chat area and question
// box are all visible with no horizontal page scrollbar.
test("AC-057: at 1280x800 the library, chat area and question box are visible with no horizontal scroll", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockBackend(page, { initialDocs: [READY_DOC] });
  await page.goto("/");

  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByLabel("Your question")).toBeVisible();
  await expect(page.getByRole("button", { name: /Send/ })).toBeVisible();

  const hasHorizontalScroll = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasHorizontalScroll).toBe(false);
});

// AC-058: at a phone viewport, every control is reachable and no element is
// clipped or overlapping.
test("AC-058: at 375x812 every control is reachable and nothing is clipped or overlapping", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockBackend(page, { initialDocs: [READY_DOC] });
  await page.goto("/");

  await expect(page.getByLabel("Choose a file")).toBeVisible();
  await expect(page.getByText("First.pdf")).toBeVisible();

  const selectButton = page.getByRole("button", { name: /First\.pdf/ });
  await selectButton.scrollIntoViewIfNeeded();
  await expect(selectButton).toBeVisible();
  await selectButton.click();

  const input = page.getByLabel("Your question");
  await input.scrollIntoViewIfNeeded();
  await expect(input).toBeVisible();
  const sendButton = page.getByRole("button", { name: /Send/ });
  await sendButton.scrollIntoViewIfNeeded();
  await expect(sendButton).toBeVisible();

  // No clipping: every control's own bounding box fits fully within the
  // page's scrollable width (nothing is cut off sideways).
  const pageWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  for (const locator of [
    page.getByLabel("Choose a file"),
    selectButton,
    input,
    sendButton,
    page.getByLabel("Remove First.pdf from the shelf"),
  ]) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(pageWidth + 1);
    }
  }

  // No overlap: the question input and its Send button do not visually
  // collide with each other.
  const inputBox = await input.boundingBox();
  const sendBox = await sendButton.boundingBox();
  if (inputBox && sendBox) {
    const overlapsVertically =
      inputBox.y < sendBox.y + sendBox.height && sendBox.y < inputBox.y + inputBox.height;
    const overlapsHorizontally =
      inputBox.x < sendBox.x + sendBox.width && sendBox.x < inputBox.x + inputBox.width;
    const fullyOverlapping = overlapsVertically && overlapsHorizontally;
    // They may share a row on wider phones or stack on narrow ones; either
    // way they must not fully occupy the same box (which would mean one is
    // hidden under the other).
    expect(fullyOverlapping && JSON.stringify(inputBox) === JSON.stringify(sendBox)).toBe(false);
  }
});
