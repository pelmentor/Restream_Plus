/**
 * Open-redirect-safe `next` path validator.
 *
 * Accept ONLY same-origin path values (must start with `/`, must not
 * start with `//` — protocol-relative URL), and must contain only
 * characters from a small allowlist. Anything else returns null and
 * the caller falls back to `/`.
 *
 * Habits matter even for a single-tenant appliance — phase-7-design-memo §B.
 */

// Allowlist: alnum, `_`, `-`, `.`, `/`, `~`, `%`, `?`, `&`, `=`, `#`,
// `:`, `+`. Whitespace and control characters are rejected by virtue
// of not appearing in the allowlist.
const SAFE_PATH_RE = /^\/[\w\-./~%?&=#:+]*$/;

export function safeNext(value: unknown): string | null {
  if (typeof value !== "string") return null;
  if (value.length === 0 || value.length > 2048) return null;
  if (value.startsWith("//")) return null;
  if (!SAFE_PATH_RE.test(value)) return null;
  return value;
}
