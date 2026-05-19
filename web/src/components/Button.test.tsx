import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";

// Slice-6 SA-RISK-4 smoke test — proves jsdom + @testing-library wiring
// is correct end-to-end. Real protection for Button comes from the
// migration tests + ad-hoc renders in the FormField suite.

describe("Button — slice-6 jsdom smoke", () => {
  it("renders the children inside a <button> with type=button by default", () => {
    render(<Button>Save</Button>);
    const btn = screen.getByRole("button", { name: "Save" });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveAttribute("type", "button");
  });

  it("loading=true sets aria-busy and disables interaction", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Saving
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "Saving" });
    expect(btn).toHaveAttribute("aria-busy", "true");
    expect(btn).toBeDisabled();
    await user.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("iconOnly renders without a text label — caller MUST supply aria-label", () => {
    render(
      <Button iconOnly aria-label="Close panel">
        <span aria-hidden="true">×</span>
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Close panel" })).toBeInTheDocument();
  });
});
