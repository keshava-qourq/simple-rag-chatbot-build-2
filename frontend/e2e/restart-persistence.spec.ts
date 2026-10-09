import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * Covers AC-027/AC-028 from the frontend's side (US-009-2): reloading the
 * page repopulates the document library from the backend (AC-027) while the
 * chat area comes back empty, because a thread is session-only React state
 * that is never persisted anywhere the frontend can read back from (AC-028)
 * -- see `frontend/src/screens/Home.tsx`'s `threads` state, which has no
 * localStorage/sessionStorage/cookie backing at all.
 *
 * Network is fully mocked, following the conventions in documents.spec.ts
 * and home-chat.spec.ts, so this suite exercises the frontend's own
 * behaviour across a reload without depending on a live backend.
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

async function mockBackend(page: Page, docs: unknown[]): Promise<void> {
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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        answer: "The document says X.",
        source: { document_name: "First.pdf", page_number: 3, chunk_index: null },
        is_fallback: false,
        model_configured: true,
      }),
    });
  });
}

test("AC-027/AC-028: reloading the app shows the library populated and the chat area empty", async ({
  page,
}) => {
  await mockBackend(page, [READY_DOC]);
  await page.goto("/");

  // Library is populated from the backend.
  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByText("Ready")).toBeVisible();

  // Build up a chat thread for the ready document.
  await page.getByRole("button", { name: /First\.pdf/ }).click();
  const input = page.getByLabel("Your question");
  await input.fill("What does clause 4 say?");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByText("What does clause 4 say?")).toBeVisible();
  await expect(page.getByText("The document says X.")).toBeVisible();

  // Reload: the mocked backend still reports the same library (AC-027),
  // but nothing restores the prior chat thread (AC-028) -- it was never
  // sent anywhere to persist, and no endpoint exists that could return it.
  await page.reload();

  await expect(page.getByText("First.pdf")).toBeVisible();
  await expect(page.getByText("Ready")).toBeVisible();

  await expect(page.getByText("What does clause 4 say?")).toHaveCount(0);
  await expect(page.getByText("The document says X.")).toHaveCount(0);

  // No document is selected after a reload, so the chat pane shows the
  // "nothing selected" empty state rather than a restored thread.
  await expect(page.getByText("Nothing selected yet")).toBeVisible();
});

test("AC-028: reloading with an empty library shows the empty shelf and no restored chat", async ({
  page,
}) => {
  await mockBackend(page, []);
  await page.goto("/");

  await expect(page.getByText("Your shelf is empty")).toBeVisible();
  await expect(page.getByText("Nothing selected yet")).toBeVisible();

  await page.reload();

  await expect(page.getByText("Your shelf is empty")).toBeVisible();
  await expect(page.getByText("Nothing selected yet")).toBeVisible();
});
