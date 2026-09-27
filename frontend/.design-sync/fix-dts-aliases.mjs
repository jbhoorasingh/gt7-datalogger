// tsc keeps the `@/…` path alias verbatim in the declarations it emits, and the
// converter's ts-morph project has no paths mapping — an aliased prop type then
// resolves to `any` and the component's props body collapses to an index
// signature. Rewrite the aliases to relative specifiers so the checker can
// follow them.

import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = "ds-types/src";

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith(".d.ts") ? [p] : [];
  });
}

let touched = 0;
for (const file of walk(ROOT)) {
  const src = readFileSync(file, "utf8");
  const out = src.replace(/(["'])@\/([^"']+)\1/g, (_m, q, spec) => {
    let rel = relative(join(file, ".."), join(ROOT, spec)).split("\\").join("/");
    if (!rel.startsWith(".")) rel = `./${rel}`;
    return `${q}${rel}${q}`;
  });
  if (out !== src) {
    writeFileSync(file, out);
    touched++;
  }
}
console.error(`fix-dts-aliases: rewrote ${touched} declaration file(s)`);
