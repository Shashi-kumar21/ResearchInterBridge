import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Content invariants only: no DOM, no network, no React renderer yet.
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
