import {
  forwardRef,
  useId,
  useMemo,
  type HTMLAttributes,
  type ReactNode,
} from "react";

import {
  FormFieldContext,
  useFormField,
} from "@/components/formFieldContext";
import type { FormFieldContextValue } from "@/components/formFieldContext";
import { cn } from "@/lib/cn";
import { Input, type InputProps } from "@/components/Input";
import { Select, type SelectProps } from "@/components/Select";
import { t } from "@/messages";

// `useFormField` + `FormFieldContextValue` live in formFieldContext.ts;
// import them from there directly. Re-exporting here would re-trigger
// react-refresh/only-export-components for this file (the rule kicks in
// on hook + component co-export).

// Slice-6 UX-F1. Compound primitive — replaces the ad-hoc
// `<label><span>…</span><Input/><p helper/><p error/></label>` pattern
// scattered across GeneralTab / VKTab / PersistentTargetTab / SecurityTab.
//
// Design contract (per UX-architect memo §1):
//   - Compound + context. No `cloneElement` (breaks forwardRef typing).
//   - Error REPLACES helper (WCAG 3.3.1 — single rank-1 cue).
//   - Required asterisk visible + sr-only "(required)" for screen
//     readers (asterisk alone is unreliable across SR engines).
//   - Always-visible label; no `hideLabel` escape hatch — operator UI
//     never needs visually-hidden labels (use Button iconOnly instead).
//
// API:
//   <FormField label="Idle timeout" helper="…" error={err}>
//     <FormField.Input {...register("idle")} />
//   </FormField>
//
//   For controls that don't accept native `id`/`aria-*` props (Slider,
//   Switch), use the render-prop form:
//   <FormField label="Idle">
//     <FormField.Control>
//       {(field) => <Slider ariaLabel={field.label} {...field.numeric} />}
//     </FormField.Control>
//   </FormField>

export interface FormFieldProps {
  readonly label: ReactNode;
  readonly helper?: ReactNode;
  // `string | undefined` (not just optional) so RHF's
  // `errors.field?.message` can be passed straight through under the
  // project's `exactOptionalPropertyTypes: true`.
  readonly error?: string | undefined;
  readonly required?: boolean;
  readonly children: ReactNode;
  readonly id?: string;
  readonly className?: string;
}

interface FormFieldExports {
  Input: typeof FormFieldInput;
  Select: typeof FormFieldSelect;
  Control: typeof FormFieldControl;
}

function FormFieldRoot(props: FormFieldProps): ReactNode {
  const { label, helper, error, required = false, children, id, className } = props;
  const reactId = useId();
  const controlId = id ?? reactId;
  const hasError = error !== undefined && error.length > 0;
  const hasHelper = helper !== undefined && !hasError;
  const errorId = hasError ? `${controlId}-error` : undefined;
  const helperId = hasHelper ? `${controlId}-helper` : undefined;
  const describedBy = errorId ?? helperId;

  const ctx = useMemo<FormFieldContextValue>(
    () => ({
      controlId,
      describedBy,
      invalid: hasError,
      errorId,
      helperId,
      required,
    }),
    [controlId, describedBy, hasError, errorId, helperId, required],
  );

  return (
    <FormFieldContext.Provider value={ctx}>
      <div className={cn("flex flex-col gap-(--space-1)", className)}>
        <label
          htmlFor={controlId}
          className="text-(length:--text-sm) font-medium text-(--color-fg-strong)"
        >
          {label}
          {required && (
            <>
              <span
                aria-hidden="true"
                className="ml-(--space-1) text-(--color-error)"
              >
                {"*"}
              </span>
              <span className="sr-only"> {t("formField.requiredSuffix")}</span>
            </>
          )}
        </label>
        {children}
        {hasError && (
          <p
            id={errorId}
            role="alert"
            className="text-(length:--text-xs) text-(--color-error)"
          >
            {error}
          </p>
        )}
        {hasHelper && (
          <p
            id={helperId}
            className="text-(length:--text-xs) text-(--color-fg-muted)"
          >
            {helper}
          </p>
        )}
      </div>
    </FormFieldContext.Provider>
  );
}

// ----- FormField.Input -----

export type FormFieldInputProps = Omit<
  InputProps,
  "id" | "aria-describedby" | "aria-invalid" | "invalid" | "required"
>;

const FormFieldInput = forwardRef<HTMLInputElement, FormFieldInputProps>(
  function FormFieldInput(props, ref) {
    const field = useFormField();
    return (
      <Input
        ref={ref}
        id={field.controlId}
        aria-describedby={field.describedBy}
        invalid={field.invalid}
        required={field.required}
        {...props}
      />
    );
  },
);

// ----- FormField.Select -----

export type FormFieldSelectProps = Omit<
  SelectProps,
  "id" | "aria-describedby" | "aria-invalid" | "invalid" | "required"
>;

const FormFieldSelect = forwardRef<HTMLSelectElement, FormFieldSelectProps>(
  function FormFieldSelect(props, ref) {
    const field = useFormField();
    return (
      <Select
        ref={ref}
        id={field.controlId}
        aria-describedby={field.describedBy}
        invalid={field.invalid}
        required={field.required}
        {...props}
      />
    );
  },
);

// ----- FormField.Control (render-prop escape hatch) -----

export interface FormFieldControlRenderProps {
  readonly id: string;
  readonly "aria-describedby": string | undefined;
  readonly "aria-invalid": true | undefined;
  readonly required: boolean;
}

export interface FormFieldControlProps {
  readonly children: (field: FormFieldControlRenderProps) => ReactNode;
}

function FormFieldControl(props: FormFieldControlProps): ReactNode {
  const field = useFormField();
  return (
    <>
      {props.children({
        id: field.controlId,
        "aria-describedby": field.describedBy,
        "aria-invalid": field.invalid ? true : undefined,
        required: field.required,
      })}
    </>
  );
}

// ----- Optional: helper text wrapper for callers that want extra
// content alongside the auto-rendered helper. Most callers won't need
// this — included for parity with Radix-style compound APIs. -----

export interface FormFieldDescriptionProps
  extends HTMLAttributes<HTMLParagraphElement> {
  readonly children: ReactNode;
}

function FormFieldDescription(props: FormFieldDescriptionProps): ReactNode {
  const { className, children, ...rest } = props;
  return (
    <p
      className={cn(
        "text-(length:--text-xs) text-(--color-fg-muted)",
        className,
      )}
      {...rest}
    >
      {children}
    </p>
  );
}

// ----- Attach the compound exports to the root -----

interface FormFieldComponent extends FormFieldExports {
  (props: FormFieldProps): ReactNode;
  Description: typeof FormFieldDescription;
}

export const FormField = FormFieldRoot as FormFieldComponent;
FormField.Input = FormFieldInput;
FormField.Select = FormFieldSelect;
FormField.Control = FormFieldControl;
FormField.Description = FormFieldDescription;
