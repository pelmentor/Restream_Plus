import { createContext, useContext } from "react";

// Slice-6: split out of FormField.tsx so the hook + context don't
// trip react-refresh/only-export-components (that rule fires when a
// file mixes hook exports with component exports, slowing HMR).

export interface FormFieldContextValue {
  readonly controlId: string;
  readonly describedBy: string | undefined;
  readonly invalid: boolean;
  readonly errorId: string | undefined;
  readonly helperId: string | undefined;
  readonly required: boolean;
}

export const FormFieldContext = createContext<FormFieldContextValue | null>(
  null,
);

export function useFormField(): FormFieldContextValue {
  const ctx = useContext(FormFieldContext);
  if (ctx === null) {
    throw new Error("useFormField must be called inside a <FormField>");
  }
  return ctx;
}
