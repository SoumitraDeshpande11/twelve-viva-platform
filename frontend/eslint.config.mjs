import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/** Flat ESLint config (ESLint 9 / Next 16). `next lint` was removed; run `eslint .`. */
const config = [
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    // Experimental React-Compiler advisories. They flag idiomatic patterns
    // (custom-hook ref passed to a DOM ref prop, mount-guard setState, mirroring
    // a prop into a ref for stable async reads). Keep them visible as warnings
    // rather than failing the build on correct code.
    rules: {
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default config;
