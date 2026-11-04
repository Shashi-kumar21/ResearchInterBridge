import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    // Logic tests run in Node. Component tests opt into jsdom with a per-file
    // `// @vitest-environment jsdom` docblock, so the pure content tests never
    // pay for a DOM.
    environment: "node",
    include: ["lib/**/*.test.ts", "components/**/*.test.tsx"],
  },
});
