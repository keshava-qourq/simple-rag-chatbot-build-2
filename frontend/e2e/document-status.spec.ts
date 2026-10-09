import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * Covers processing/ready/failed status rendering and document-library
 * behaviour (US-004-2). Network is fully mocked per the packet's contract:
 * GET /documents is driven through processing -> ready -> failed
 * transitions without a live backend, following the conventions in
 * documents.spec.ts.
 */

const CONFIG_RESPONSE = {
  llm_configured: true,
  embedding_configured: true,
  llm_model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  issues: [],
};

interface MockOptions {
  initialDocs?: unknown[];
  /** Called on every GET /documents; returns the body for that call. */
  onGet?: (callCount: number, docs: unknown[]) => unknown[];
  /** Overrides the default 202 success behaviour for POST /documents. */
  onPost?: (route: Route, docs: unknown[]) => Promise<boolean>;
}

async function mockBackend(page: Page, options: MockOptions = {}): Promise<void> {
  const docs = options.initialDocs ? [...options.initialDocs] : [];
  let getCalls = 0;

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
      const body = options.onGet ? options.onGet(getCalls, docs) : docs;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
      return;
    }
    if (request.method() === "POST") {
      if (options.onPost) {
        const handled = await options.onPost(route, docs);
        if (handled) return;
      }
      const created = {
        id: `doc-${docs.length + 1}`,
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
      docs.unshift(created);
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }
    await route.continue();
  });
}

// AC-011: after upload, the file name appears with an in-progress status and
// a visible loading indicator.
test("AC-011: uploaded file shows with a processing status and a visible spinner", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");
  await expect(page.getByText("Your shelf is empty")).toBeVisible();

  await page.getByLabel("Choose a file").setInputFiles({
    name: "contract.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("terms and conditions"),
  });

  await expect(page.getByText("contract.txt")).toBeVisible();
  await expect(page.getByText("Processing")).toBeVisible();
  await expect(page.locator(".animate-spin").first()).toBeVisible();
});

// AC-012: when the API reports ready, the status reads ready, the spinner is
// gone and the row becomes selectable.
test("AC-012: a document transitions from processing to ready and becomes selectable", async ({
  page,
}) => {
  const processingDoc = {
    id: "doc-1",
    file_name: "Policy.pdf",
    file_type: "pdf",
    status: "processing",
    error_message: null,
    chunk_count: 0,
    created_at: "2026-10-09T00:00:00Z",
  };
  await mockBackend(page, {
    initialDocs: [processingDoc],
    onGet: (callCount, docs) => {
      if (callCount === 1) return docs;
      return docs.map((d) => ({ ...(d as object), status: "ready", chunk_count: 9 }));
    },
  });
  await page.goto("/");

  await expect(page.getByText("Policy.pdf")).toBeVisible();
  await expect(page.getByText("Processing")).toBeVisible();

  await expect(page.getByText("Ready")).toBeVisible({ timeout: 6000 });
  await expect(page.locator(".animate-spin")).toHaveCount(0);

  const selectButton = page.getByRole("button", { name: /Policy\.pdf/ });
  await selectButton.click();
  await expect(selectButton).toHaveAttribute("aria-current", "true");
  await expect(page.getByLabel("Your question")).toBeEnabled();
});

// AC-013: a failed document shows the failed status with the backend's
// specific reason, and the question input/send stay disabled for it.
test("AC-013: a failed document shows its specific reason and keeps questions disabled", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
      {
        id: "doc-2",
        file_name: "Scanned.pdf",
        file_type: "pdf",
        status: "failed",
        error_message: "Could not extract text: the PDF is encrypted.",
        chunk_count: 0,
        created_at: "2026-10-03T00:00:00Z",
      },
    ],
  });
  await page.goto("/");

  await expect(page.getByText("Scanned.pdf")).toBeVisible();
  await expect(page.getByText("Failed")).toBeVisible();
  await expect(
    page.getByText("Could not extract text: the PDF is encrypted."),
  ).toBeVisible();

  // A failed row is not selectable, so no document ends up selected and the
  // question controls stay disabled.
  await expect(page.getByLabel("Your question")).toBeDisabled();
  await expect(page.getByRole("button", { name: /Send/ })).toBeDisabled();
});

// AC-018: with two documents listed, both show name and status and the
// selected one is visually indicated.
test("AC-018: two documents both show name/status, and selecting one marks it", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
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
  await page.goto("/");

  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByText("Second.txt")).toBeVisible();
  await expect(page.getByText("Ready")).toHaveCount(2);

  const firstBtn = page.getByRole("button", { name: /First\.pdf/ });
  const secondBtn = page.getByRole("button", { name: /Second\.txt/ });

  await expect(firstBtn).not.toHaveAttribute("aria-current", "true");
  await expect(secondBtn).not.toHaveAttribute("aria-current", "true");

  await secondBtn.click();

  await expect(secondBtn).toHaveAttribute("aria-current", "true");
  await expect(firstBtn).not.toHaveAttribute("aria-current", "true");
});

// AC-019: uploading a second document leaves the first row present and
// unchanged; both are listed afterwards.
test("AC-019: uploading a second document leaves the first row unchanged", async ({ page }) => {
  await mockBackend(page, {
    initialDocs: [
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
  await page.goto("/");
  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByText("4 chunks indexed")).toBeVisible();

  await page.getByLabel("Choose a file").setInputFiles({
    name: "Second.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("more content"),
  });

  await expect(page.getByText("Second.txt")).toBeVisible();
  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByText("4 chunks indexed")).toBeVisible();
  await expect(page.getByText("2 files")).toBeVisible();
});

// AC-020: an empty library shows the upload invitation and no error.
test("AC-020: an empty library shows the upload invitation and no error", async ({ page }) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");

  await expect(page.getByText("Your shelf is empty")).toBeVisible();
  await expect(
    page.getByText("Upload a PDF, DOCX or TXT file above to start asking questions."),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// AC-017 (UI half): dismissing an upload error leaves the selection and the
// rest of the library intact.
test("AC-017: dismissing an upload error preserves selection and the rest of the library", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
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
    onPost: async (route) => {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Server storage quota exceeded." }),
      });
      return true;
    },
  });
  await page.goto("/");

  const firstBtn = page.getByRole("button", { name: /First\.pdf/ });
  await firstBtn.click();
  await expect(firstBtn).toHaveAttribute("aria-current", "true");

  await page.getByLabel("Choose a file").setInputFiles({
    name: "big.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("x".repeat(100)),
  });

  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Server storage quota exceeded.");

  await page.getByLabel("Dismiss upload error").click();

  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(firstBtn).toHaveAttribute("aria-current", "true");
  await expect(page.getByText("Second.txt")).toBeVisible();
});
