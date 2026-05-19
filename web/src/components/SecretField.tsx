import { useEffect, useRef, useState, type ReactNode } from "react";
import { Eye, EyeSlash, Lock } from "@phosphor-icons/react";

import { Button } from "./Button";
import { Input } from "./Input";
import { cn } from "@/lib/cn";
import { t } from "@/messages";

export type SecretFieldVariant = "masked" | "entry" | "paste_only";

export interface MaskedFieldProps {
  readonly variant: "masked";
  readonly last4: string | null;
  readonly revealedValue?: string | undefined;
  readonly revealCountdownLabel?: string | undefined;
  readonly onRequestReveal?: (() => void) | undefined;
  readonly onHide?: (() => void) | undefined;
  readonly ariaLabel: string;
  readonly disabled?: boolean | undefined;
}

export interface EntryFieldProps {
  readonly variant: "entry";
  readonly value: string;
  readonly onChange: (v: string) => void;
  readonly ariaLabel: string;
  readonly disabled?: boolean | undefined;
  readonly initiallyRevealed?: boolean | undefined;
  readonly autoComplete?: string | undefined;
}

export interface PasteOnlyFieldProps {
  readonly variant: "paste_only";
  readonly value: string;
  readonly onChange: (v: string) => void;
  readonly ariaLabel: string;
  readonly disabled?: boolean | undefined;
  readonly helper?: ReactNode;
}

export type SecretFieldProps =
  | MaskedFieldProps
  | EntryFieldProps
  | PasteOnlyFieldProps;

/**
 * Design-system §6.10 + phase-9-design-memo §D1.
 *
 * Three variants:
 *  - masked    — `•••• 7F2A`; eye click triggers AuthReprompt + reveal
 *                with 60 s countdown; never persists plaintext.
 *  - entry     — plain password input with show/hide toggle. Used inside
 *                OneTimeRevealBanner (pre-revealed) and Change-key form.
 *  - paste_only — single-use input, autoComplete="new-password". No
 *                 reveal toggle, no persistence.
 */
export function SecretField(props: SecretFieldProps): ReactNode {
  if (props.variant === "masked") return <MaskedField {...props} />;
  if (props.variant === "entry") return <EntryField {...props} />;
  return <PasteOnlyField {...props} />;
}

function MaskedField(props: MaskedFieldProps): ReactNode {
  const {
    last4,
    revealedValue,
    revealCountdownLabel,
    onRequestReveal,
    onHide,
    ariaLabel,
    disabled = false,
  } = props;
  const isRevealed = revealedValue !== undefined;
  const display =
    last4 === null
      ? "—"
      : isRevealed
        ? revealedValue
        : `•••• •••• •••• ${last4}`;
  const Icon = disabled ? Lock : isRevealed ? Eye : EyeSlash;
  return (
    <div className="relative">
      <div
        role="textbox"
        aria-readonly="true"
        aria-label={ariaLabel}
        className={cn(
          // Slice-5: bumped from h-10 (40px) to control-lg (44px) so the
          // display row matches the trailing reveal Button's h-11.
          "flex h-(--size-control-lg) items-center rounded-(--radius-md) border bg-(--color-bg-base)",
          "border-(--color-border-subtle) px-(--space-3)",
          "font-mono text-(length:--text-sm) text-(--color-fg-strong) tabular-nums",
          disabled && "bg-(--color-bg-disabled) text-(--color-fg-muted)",
        )}
      >
        <span className="flex-1 truncate">{display}</span>
        {revealCountdownLabel !== undefined && (
          <span className="ml-(--space-2) shrink-0 text-(length:--text-2xs) text-(--color-warn)">
            {revealCountdownLabel}
          </span>
        )}
      </div>
      {!disabled && onRequestReveal !== undefined && last4 !== null && (
        <Button
          type="button"
          onClick={isRevealed ? onHide : onRequestReveal}
          aria-label={isRevealed ? t("secret.hideAria") : t("secret.revealAria")}
          aria-pressed={isRevealed}
          variant="ghost"
          size="lg"
          iconOnly
          className="absolute top-1/2 right-1 -translate-y-1/2 text-(--color-fg-muted)"
        >
          <Icon className="h-5 w-5" weight="regular" aria-hidden="true" />
        </Button>
      )}
      {disabled && (
        <p className="mt-(--space-1) text-(length:--text-xs) text-(--color-fg-muted)">
          {t("secret.lockedHelper")}
        </p>
      )}
    </div>
  );
}

function EntryField(props: EntryFieldProps): ReactNode {
  const {
    value,
    onChange,
    ariaLabel,
    disabled = false,
    initiallyRevealed = false,
    autoComplete = "new-password",
  } = props;
  const [revealed, setRevealed] = useState(initiallyRevealed);
  return (
    <Input
      type={revealed ? "text" : "password"}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={ariaLabel}
      autoComplete={autoComplete}
      spellCheck={false}
      data-1p-ignore="true"
      disabled={disabled}
      mono
      size="lg"
      trailing={
        <Button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          aria-label={revealed ? t("secret.hideAria") : t("secret.revealAria")}
          aria-pressed={revealed}
          variant="ghost"
          size="lg"
          iconOnly
          className="text-(--color-fg-muted)"
        >
          {revealed ? (
            <Eye className="h-5 w-5" weight="regular" aria-hidden="true" />
          ) : (
            <EyeSlash className="h-5 w-5" weight="regular" aria-hidden="true" />
          )}
        </Button>
      }
    />
  );
}

function PasteOnlyField(props: PasteOnlyFieldProps): ReactNode {
  const { value, onChange, ariaLabel, disabled = false, helper } = props;
  const ref = useRef<HTMLInputElement | null>(null);
  // Defensive: clear on unmount so the React fiber's last value
  // doesn't linger in retained-heap snapshots. Capture the node
  // reference at mount time per react-hooks/exhaustive-deps.
  useEffect(() => {
    const node = ref.current;
    return () => {
      if (node) node.value = "";
    };
  }, []);
  return (
    <div>
      <Input
        ref={ref}
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={ariaLabel}
        autoComplete="new-password"
        spellCheck={false}
        data-1p-ignore="true"
        disabled={disabled}
        mono
        size="lg"
      />
      {helper && (
        <p className="mt-(--space-1) text-(length:--text-xs) text-(--color-fg-muted)">
          {helper}
        </p>
      )}
    </div>
  );
}
