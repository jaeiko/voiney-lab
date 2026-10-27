import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Shared CI/lane hosts can queue a React render beyond the library's 1 s default.
configure({ asyncUtilTimeout: 15000 });

afterEach(() => {
  cleanup();
});
