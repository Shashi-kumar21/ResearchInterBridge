import next from "eslint-config-next";

const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  ...next,
  {
    rules: {
      // Scraped professor/paper text shown in later phases must stay inert.
      // This rule stops raw HTML injection; it cannot see `href`/`src` pointing
      // at untrusted URLs, so untrusted links are validated in code as well.
      "react/no-danger": "error",
    },
  },
];

export default config;
