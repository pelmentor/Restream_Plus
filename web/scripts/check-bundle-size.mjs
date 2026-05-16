#!/usr/bin/env node
/**
 * Post-build bundle-size guard.
 *
 * Reads every JS + CSS file under dist/assets/, gzips each in-memory,
 * sums the bytes. Fails (exit 1) if the total exceeds the budget set
 * in ADR-0002 / docs/CODE_PLAN.md Phase 7 acceptance criteria.
 *
 * Phase 11 CI inherits this guard for free via `npm run build`.
 */

import { gzipSync } from "node:zlib";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const BUDGET_BYTES = 250 * 1024;

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST_DIR = resolve(__dirname, "..", "dist", "assets");

/** @returns {string[]} */
function walk(dir) {
  /** @type {string[]} */
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      out.push(...walk(full));
    } else {
      out.push(full);
    }
  }
  return out;
}

function main() {
  if (!statSync(DIST_DIR, { throwIfNoEntry: false })?.isDirectory()) {
    console.error(`[bundle-check] FAIL: dist/assets not found at ${DIST_DIR}`);
    process.exit(1);
  }

  const files = walk(DIST_DIR)
    .filter((f) => f.endsWith(".js") || f.endsWith(".css"))
    .map((f) => {
      const raw = readFileSync(f);
      const gz = gzipSync(raw);
      return { file: f, ext: extname(f), raw: raw.byteLength, gz: gz.byteLength };
    })
    .sort((a, b) => b.gz - a.gz);

  const total = files.reduce((acc, f) => acc + f.gz, 0);

  console.log("[bundle-check] gzipped sizes:");
  for (const f of files) {
    const rel = f.file.replace(DIST_DIR + "\\", "").replace(DIST_DIR + "/", "");
    console.log(
      `  ${rel.padEnd(48)}  ${f.gz.toString().padStart(8)} B (raw ${f.raw} B)`,
    );
  }
  console.log(`[bundle-check] total gzipped: ${total} B (budget ${BUDGET_BYTES} B)`);

  if (total > BUDGET_BYTES) {
    console.error(
      `[bundle-check] FAIL: total ${total} B exceeds budget ${BUDGET_BYTES} B by ${total - BUDGET_BYTES} B`,
    );
    process.exit(1);
  }
  console.log("[bundle-check] OK");
}

main();
