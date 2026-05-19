import { describe, expect, it } from "vitest";

import { cn } from "./cn";

// Slice-6 SA-RISK-3 regression test. The slice-4 CRIT-1 was a
// className override that the variant's default `text-(--color-fg-default)`
// silently shadowed because Tailwind v4 emits `text-(--color-error)`
// before `text-(--color-fg-default)` in source order, leaving the
// variant default to win at equal specificity.
//
// `tailwind-merge` resolves this at the cn() boundary: when two
// utilities target the same CSS property, the LAST one in the
// concatenated list wins. These tests pin the contract.

describe("cn() with tailwind-merge", () => {
  it("class precedence: caller's text-color wins over variant default", () => {
    const result = cn("text-(--color-fg-default)", "text-(--color-error)");
    expect(result).toBe("text-(--color-error)");
  });

  it("class precedence: variant bg drops out when caller overrides", () => {
    const result = cn("bg-(--color-bg-elevated)", "bg-(--color-error-faint)");
    expect(result).toBe("bg-(--color-error-faint)");
  });

  it("non-conflicting utilities pass through untouched", () => {
    const result = cn("flex items-center", "gap-2");
    expect(result).toBe("flex items-center gap-2");
  });

  it("falsy values are dropped (clsx behavior preserved)", () => {
    const result = cn("base", false, null, undefined, "extra");
    expect(result).toBe("base extra");
  });

  it("conditional class arrays compose correctly", () => {
    const isActive = true;
    const isDisabled = false;
    const result = cn(
      "btn",
      isActive && "active",
      isDisabled && "disabled",
    );
    expect(result).toBe("btn active");
  });

  it("hover variants of the same property also dedupe", () => {
    const result = cn(
      "hover:bg-(--color-bg-elevated)",
      "hover:bg-(--color-bg-sunken)",
    );
    expect(result).toBe("hover:bg-(--color-bg-sunken)");
  });
});
