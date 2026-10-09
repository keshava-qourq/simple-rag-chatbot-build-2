// Loaded by Vitest before every test file (see `test.setupFiles` in
// vite.config.ts).
//
// jest-dom adds the matchers a component test is actually written in --
// toBeInTheDocument, toBeDisabled, toHaveValue. Without them a test either
// asserts something weaker than it means or fails to compile, and the agent
// writing it has no way to tell which.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Every test starts on an empty document. Vitest does not unmount between
// tests, and a component left mounted makes the next `getByRole` ambiguous --
// which reads as a broken component rather than a dirty fixture.
afterEach(cleanup);
