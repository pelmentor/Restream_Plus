import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";

import { Button } from "./Button";
import { Input } from "./Input";
import { apiFetch, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  RepromptGrantResponse,
  type RepromptScopeT,
} from "@/lib/schemas/auth";
import { t } from "@/messages";
import {
  AuthRepromptContext,
  REPROMPT_BUSY,
  REPROMPT_CANCELLED,
  type RepromptFn,
} from "@/hooks/useAuthReprompt";

interface PendingState {
  scope: RepromptScopeT;
  resolve: (grantId: string) => void;
  reject: (err: unknown) => void;
  triggerEl: HTMLElement | null;
}

/**
 * Singleton context provider + dialog body. Mount once inside
 * `<RequireAuth>`, as a sibling of `<StatusStreamHost>` and
 * `<AppShell>` (phase-9-design-memo §C / §U.1).
 *
 * Concurrent calls reject the second with `REPROMPT_BUSY` — there's no
 * UX in which two destructive ops should fire in parallel.
 */
export function AuthRepromptHost({
  children,
}: {
  readonly children: ReactNode;
}): ReactNode {
  const [pending, setPending] = useState<PendingState | null>(null);
  const pendingRef = useRef<PendingState | null>(null);
  pendingRef.current = pending;

  const request = useCallback<RepromptFn>(
    (scope: RepromptScopeT) =>
      new Promise<string>((resolve, reject) => {
        if (pendingRef.current !== null) {
          reject(
            new ApiError({ code: "unknown", status: 0, message: REPROMPT_BUSY }),
          );
          return;
        }
        const triggerEl =
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        setPending({ scope, resolve, reject, triggerEl });
      }),
    [],
  );

  const handleClose = useCallback(() => {
    if (pending === null) return;
    pending.reject(
      new ApiError({
        code: "unknown",
        status: 0,
        message: REPROMPT_CANCELLED,
      }),
    );
    pending.triggerEl?.focus();
    setPending(null);
  }, [pending]);

  const handleSuccess = useCallback(
    (grantId: string) => {
      if (pending === null) return;
      pending.resolve(grantId);
      pending.triggerEl?.focus();
      setPending(null);
    },
    [pending],
  );

  return (
    <AuthRepromptContext.Provider value={request}>
      {children}
      {pending !== null && (
        <AuthRepromptDialog
          scope={pending.scope}
          onSuccess={handleSuccess}
          onCancel={handleClose}
        />
      )}
    </AuthRepromptContext.Provider>
  );
}

interface AuthRepromptDialogProps {
  readonly scope: RepromptScopeT;
  readonly onSuccess: (grantId: string) => void;
  readonly onCancel: () => void;
}

function scopeBodyKey(
  scope: RepromptScopeT,
):
  | "reprompt.bodyRevealStreamKey"
  | "reprompt.bodyRevealIngestKey"
  | "reprompt.bodyRegenerateIngestKey"
  | "reprompt.bodyRotatePassphrase"
  | "reprompt.bodyChangePassword"
  | "reprompt.bodyDeleteTarget"
  | "reprompt.bodyRevokeApiToken"
  | "reprompt.bodyClearCredential"
  | "reprompt.bodyResetTargetWorker" {
  switch (scope) {
    case "reveal_stream_key":
      return "reprompt.bodyRevealStreamKey";
    case "reveal_ingest_key":
      return "reprompt.bodyRevealIngestKey";
    case "regenerate_ingest_key":
      return "reprompt.bodyRegenerateIngestKey";
    case "rotate_passphrase":
      return "reprompt.bodyRotatePassphrase";
    case "change_password":
      return "reprompt.bodyChangePassword";
    case "delete_target":
      return "reprompt.bodyDeleteTarget";
    case "revoke_api_token":
      return "reprompt.bodyRevokeApiToken";
    case "clear_credential":
      return "reprompt.bodyClearCredential";
    case "reset_target_worker":
      return "reprompt.bodyResetTargetWorker";
  }
}

function AuthRepromptDialog(props: AuthRepromptDialogProps): ReactNode {
  const { scope, onSuccess, onCancel } = props;
  const [password, setPassword] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const [error, setError] = useState<string>("");
  const [retryAfter, setRetryAfter] = useState<number | null>(null);

  useEffect(() => {
    if (retryAfter === null || retryAfter <= 0) return;
    const id = setInterval(() => {
      // Reset to null once the countdown reaches 0 so the form
      // re-enables — leaving it at 0 leaves the submit guard
      // (`retryAfter !== null`) blocking submission indefinitely
      // (reviewer M-1).
      setRetryAfter((s) => (s === null || s <= 1 ? null : s - 1));
    }, 1000);
    return () => clearInterval(id);
  }, [retryAfter]);

  const onSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (inFlight || retryAfter !== null) return;
    setError("");
    setInFlight(true);
    try {
      const r = (await apiFetch(
        "auth/reprompt",
        {
          method: "POST",
          json: { password, scope },
          silenceGlobalErrors: true,
        },
        RepromptGrantResponse,
      ));
      onSuccess(r.grant_id);
    } catch (e0) {
      const err = e0 as ApiError;
      if (err.status === 401) {
        setError(t("reprompt.invalidPassword"));
        setPassword("");
      } else if (err.status === 429) {
        setRetryAfter(err.retryAfter ?? 60);
      } else if (err.code === "network_error") {
        setError(t("reprompt.networkError"));
      } else {
        setError(t("common.unexpectedError"));
      }
    } finally {
      setInFlight(false);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-(--color-bg-overlay)" />
        <Dialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
            "w-[90vw] max-w-(--width-dialog-md) rounded-(--radius-lg) border bg-(--color-bg-base)",
            "border-(--color-border-subtle) p-(--space-6) shadow-(--shadow-lg)",
          )}
        >
          <Dialog.Title className="text-(length:--text-xl) font-semibold text-(--color-fg-strong)">
            {t("reprompt.title")}
          </Dialog.Title>
          <Dialog.Description className="mt-(--space-1) text-(length:--text-sm) text-(--color-fg-muted)">
            {t(scopeBodyKey(scope))}
          </Dialog.Description>
          {retryAfter !== null && retryAfter > 0 ? (
            <div
              className={cn(
                "mt-(--space-4) rounded-(--radius-md) border p-(--space-3)",
                "border-(--color-warn) bg-(--color-warn-faint) text-(length:--text-sm) text-(--color-fg-strong)",
              )}
            >
              <p className="font-semibold">{t("reprompt.rateLimitedTitle")}</p>
              <p className="mt-(--space-1)">
                {t("reprompt.rateLimitedBody", { seconds: retryAfter })}
              </p>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="mt-(--space-4)">
              <label
                htmlFor="reprompt-password"
                className="block text-(length:--text-sm) font-medium text-(--color-fg-strong)"
              >
                {t("reprompt.passwordLabel")}
              </label>
              <Input
                id="reprompt-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus
                disabled={inFlight}
                className="mt-(--space-1)"
              />
              <div
                aria-live="polite"
                className="mt-(--space-2) min-h-[1.25rem] text-(length:--text-xs) text-(--color-error)"
              >
                {error}
              </div>
              <div className="mt-(--space-5) flex justify-end gap-(--space-3)">
                <Button variant="ghost" size="md" onClick={onCancel}>
                  {t("reprompt.cancel")}
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  size="md"
                  loading={inFlight}
                  disabled={password.length === 0}
                >
                  {inFlight ? t("reprompt.confirming") : t("reprompt.confirm")}
                </Button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
