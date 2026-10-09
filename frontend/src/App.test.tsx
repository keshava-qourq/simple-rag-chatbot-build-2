import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("App", () => {
  it("renders without crashing and shows the first screen's nav link", () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    // getAllByText, not getByText: the first screen's page content may repeat
    // the text its nav link carries, and two matches must not fail the smoke test.
    expect(screen.getAllByText("RAG Chatbot").length).toBeGreaterThan(0);
  });
});
