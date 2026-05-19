import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FormField } from "./FormField";

describe("FormField — slice-6 a11y contract", () => {
  it("wires label htmlFor → input id automatically", () => {
    render(
      <FormField label="Idle timeout">
        <FormField.Input data-testid="ctrl" />
      </FormField>,
    );
    const input = screen.getByTestId("ctrl");
    const label = screen.getByText("Idle timeout");
    expect(label).toHaveAttribute("for", input.getAttribute("id") ?? "");
  });

  it("threads helper text via aria-describedby", () => {
    render(
      <FormField label="Idle timeout" helper="In seconds, applied at boot.">
        <FormField.Input data-testid="ctrl" />
      </FormField>,
    );
    const input = screen.getByTestId("ctrl");
    const describedBy = input.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const helperEl = describedBy ? document.getElementById(describedBy) : null;
    expect(helperEl).toHaveTextContent("In seconds, applied at boot.");
  });

  it("error replaces helper and sets aria-invalid on the control", () => {
    render(
      <FormField
        label="New password"
        helper="At least 10 characters."
        error="Passwords differ."
      >
        <FormField.Input data-testid="ctrl" />
      </FormField>,
    );
    const input = screen.getByTestId("ctrl");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Passwords differ.");
    // Helper text must NOT be in the document when error is shown.
    expect(screen.queryByText("At least 10 characters.")).not.toBeInTheDocument();
  });

  it("required asterisk is aria-hidden; sr-only suffix is the announced cue", () => {
    render(
      <FormField label="Email" required>
        <FormField.Input data-testid="ctrl" />
      </FormField>,
    );
    const asterisk = screen.getByText("*");
    expect(asterisk).toHaveAttribute("aria-hidden", "true");
    // sr-only suffix should be present in DOM
    expect(screen.getByText("(required)")).toBeInTheDocument();
  });

  it("FormField.Select renders typed options and reflects value", async () => {
    const user = userEvent.setup();
    let captured = "";
    render(
      <FormField label="Endpoint">
        <FormField.Select
          options={[
            { value: "a", label: "Alpha" },
            { value: "b", label: "Bravo" },
          ]}
          onChange={(e) => {
            captured = e.target.value;
          }}
          defaultValue="a"
        />
      </FormField>,
    );
    const select = screen.getByRole("combobox", { name: "Endpoint" });
    expect(select).toBeInTheDocument();
    await user.selectOptions(select, "b");
    expect(captured).toBe("b");
  });

  it("FormField.Control render-prop hands the wired ARIA props to arbitrary controls", () => {
    let captured: {
      id?: string;
      describedBy?: string | undefined;
      invalid?: true | undefined;
    } = {};
    render(
      <FormField label="Custom" helper="Help">
        <FormField.Control>
          {(field) => {
            captured = {
              id: field.id,
              describedBy: field["aria-describedby"],
              invalid: field["aria-invalid"],
            };
            return <input data-testid="custom" id={field.id} />;
          }}
        </FormField.Control>
      </FormField>,
    );
    const ctrl = screen.getByTestId("custom");
    expect(captured.id).toBe(ctrl.getAttribute("id"));
    expect(captured.describedBy).toBeDefined();
    expect(captured.invalid).toBeUndefined();
  });
});
