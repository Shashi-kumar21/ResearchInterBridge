import next from "eslint-config-next";

const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  ...next,
  {
    rules: {
      // Scraped professor/paper text shown in later phases must stay inert;
      // this rule is the lint-level half of that rule (CSP is the other half).
      "react/no-danger": "error",
    },
  },
];

export default config;
