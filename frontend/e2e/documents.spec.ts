import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * Network for the documents/config endpoints is stubbed throughout this file
 * (per the packet's constraint) so these tests exercise the frontend's own
 * behaviour -- upload validation, list rendering, empty state, deletion --
 * without depending on a live backend, LLM provider or real file
 * persistence.
 */

const CONFIG_RESPONSE = {
  llm_configured: true,
  embedding_configured: true,
  llm_model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  issues: [],
};

async function mockBackend(
  page: Page,
  options: { initialDocs?: unknown[] } = {},
): Promise<{ docs: unknown[] }> {
  const state = { docs: options.initialDocs ? [...options.initialDocs] : [] };

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
        body: JSON.stringify(state.docs),
      });
      return;
    }
    if (request.method() === "POST") {
      const created = {
        id: `doc-${state.docs.length + 1}`,
        file_name: "upload-placeholder",
        file_type: "txt",
        status: "processing",
        created_at: new Date().toISOString(),
      };
      // Best-effort: pull the real filename out of the multipart body so the
      // response matches what was actually selected.
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

  // Single-document routes: DELETE /documents/{id} (204, or 404 for an
  // unknown id) and POST /documents/{id}/ask (stubbed grounded answer).
  await page.route("**/documents/*", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const segments = url.pathname.split("/").filter(Boolean);
    const isAsk = segments[segments.length - 1] === "ask";
    const docId = isAsk ? segments[segments.length - 2] : segments[segments.length - 1];

    if (request.method() === "DELETE") {
      const exists = state.docs.some((d) => (d as { id: string }).id === docId);
      if (!exists) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Document not found" }),
        });
        return;
      }
      state.docs = state.docs.filter((d) => (d as { id: string }).id !== docId);
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (isAsk && request.method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          answer: "This is a stubbed grounded answer.",
          source: { document_name: "doc", page_number: null, chunk_index: 0 },
          is_fallback: true,
          model_configured: true,
        }),
      });
      return;
    }

    if (request.method() === "GET") {
      const found = state.docs.find((d) => (d as { id: string }).id === docId);
      if (!found) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Document not found" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(found),
      });
      return;
    }

    await route.continue();
  });

  return state;
}

test("fresh page: upload is the primary call to action and the library shows its empty state, not an error", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");

  await expect(page.getByLabel("Choose a file")).toBeVisible();
  await expect(page.getByText("Your shelf is empty")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("selecting a supported file adds it to the list with a processing status", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");
  await expect(page.getByText("Your shelf is empty")).toBeVisible();

  await page.getByLabel("Choose a file").setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello world"),
  });

  await expect(page.getByText("notes.txt")).toBeVisible();
  await expect(page.getByText("Processing")).toBeVisible();
});

test("selecting an unsupported file type shows a readable error naming PDF, DOCX and TXT and adds nothing", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  await page.goto("/");
  await expect(page.getByText("Your shelf is empty")).toBeVisible();

  await page.getByLabel("Choose a file").setInputFiles({
    name: "sheet.xlsx",
    mimeType: "application/vnd.ms-excel",
    buffer: Buffer.from("not a real spreadsheet"),
  });

  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("PDF");
  await expect(alert).toContainText("DOCX");
  await expect(alert).toContainText("TXT");

  await expect(page.getByText("sheet.xlsx")).toHaveCount(0);
  await expect(page.getByText("Your shelf is empty")).toBeVisible();
});

test("the page loads straight to the full interface with no login, registration or payment step", async ({
  page,
}) => {
  await mockBackend(page, { initialDocs: [] });
  const response = await page.goto("/");
  expect(response?.ok()).toBeTruthy();

  await expect(page.getByRole("heading", { name: "Reading Room" })).toBeVisible();
  await expect(page.getByLabel("Choose a file")).toBeVisible();

  for (const term of [
    "log in",
    "log out",
    "sign in",
    "sign up",
    "register",
    "password",
    "subscribe",
    "checkout",
    "payment",
    "credit card",
  ]) {
    await expect(page.getByText(new RegExp(term, "i"))).toHaveCount(0);
  }
});

test("no UI control for chunk size, overlap or retrieval count exists anywhere on the page", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
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
  await page.goto("/");
  await expect(page.getByText("Report.pdf")).toBeVisible();

  for (const term of [
    "chunk size",
    "overlap",
    "retrieval count",
    "top k",
    "top-k",
    "chunks to retrieve",
  ]) {
    await expect(page.getByText(new RegExp(term, "i"))).toHaveCount(0);
  }

  // No numeric/range input controls beyond the file picker and the question box.
  const inputs = page.locator("input");
  const count = await inputs.count();
  for (let i = 0; i < count; i += 1) {
    const type = await inputs.nth(i).getAttribute("type");
    expect(type === "number" || type === "range").toBeFalsy();
  }
});

// --- Deletion (US-008-1 / AC-024, AC-026) -----------------------------

test("using the clear-document control and confirming removes the row from the library", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
      {
        id: "doc-1",
        file_name: "Report.pdf",
        file_type: "pdf",
        status: "ready",
        error_message: null,
        chunk_count: 3,
        created_at: "2026-10-03T00:00:00Z",
      },
    ],
  });
  await page.goto("/");
  await expect(page.getByText("Report.pdf")).toBeVisible();

  await page.getByRole("button", { name: "Remove Report.pdf from the shelf" }).click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Delete permanently" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Report.pdf")).toHaveCount(0);
  await expect(page.getByText("Your shelf is empty")).toBeVisible();
});

test("cancelling the delete confirmation keeps the document in the library", async ({ page }) => {
  await mockBackend(page, {
    initialDocs: [
      {
        id: "doc-1",
        file_name: "Report.pdf",
        file_type: "pdf",
        status: "ready",
        error_message: null,
        chunk_count: 3,
        created_at: "2026-10-03T00:00:00Z",
      },
    ],
  });
  await page.goto("/");
  await expect(page.getByText("Report.pdf")).toBeVisible();

  await page.getByRole("button", { name: "Remove Report.pdf from the shelf" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Report.pdf")).toBeVisible();
});

test("deleting the currently selected document discards its thread and leaves an empty selection with no error", async ({
  page,
}) => {
  await mockBackend(page, {
    initialDocs: [
      {
        id: "doc-1",
        file_name: "Report.pdf",
        file_type: "pdf",
        status: "ready",
        error_message: null,
        chunk_count: 3,
        created_at: "2026-10-03T00:00:00Z",
      },
    ],
  });
  await page.goto("/");

  // Select the document and ask a question so it has an active thread.
  await page.getByRole("button", { name: /Report\.pdf/ }).click();
  await page.getByLabel("Your question").fill("What does it say?");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText("This is a stubbed grounded answer.")).toBeVisible();

  // Delete the selected document via the chat panel's own Remove control.
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Delete permanently" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("No document selected")).toBeVisible();
  await expect(page.getByText("Nothing selected yet")).toBeVisible();
  await expect(page.getByText("This is a stubbed grounded answer.")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
