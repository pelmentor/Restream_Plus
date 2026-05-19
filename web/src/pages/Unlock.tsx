import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Banner } from "@/components/Banner";
import { Button } from "@/components/Button";
import { Input } from "@/components/Input";
import { AuthLayout } from "@/components/AuthLayout";
import { apiFetch, type ApiError } from "@/lib/api";
import { safeNext } from "@/lib/safeNext";
import { UnlockResponse, type UnlockResponseT } from "@/lib/schemas/auth";
import { t } from "@/messages";

const RATE_LIMIT_FALLBACK_SECONDS = 60;

export function UnlockPage(): ReactNode {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lockoutRemaining, setLockoutRemaining] = useState<number | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    document.title = t("unlock.title");
  }, []);

  useEffect(() => {
    if (lockoutRemaining === null || lockoutRemaining <= 0) return;
    const id = window.setInterval(() => {
      setLockoutRemaining((s) => {
        if (s === null) return null;
        const next = s - 1;
        if (next <= 0) {
          setSubmitError(null);
          return null;
        }
        return next;
      });
    }, 1_000);
    return () => window.clearInterval(id);
  }, [lockoutRemaining]);

  const mutation = useMutation<UnlockResponseT, ApiError, { passphrase: string }>({
    // meta.silenceGlobalErrors — review M-1.
    meta: { silenceGlobalErrors: true },
    mutationFn: (body) =>
      apiFetch(
        "/api/unlock",
        { method: "POST", json: body, silenceGlobalErrors: true },
        UnlockResponse,
      ),
    onSuccess: () => {
      // Force the session probe to re-run; RequireAuth will then
      // either render the destination or bounce to /login.
      void queryClient.invalidateQueries({ queryKey: ["auth", "session"] });
      const fromState = (location.state as { from?: unknown } | null)?.from;
      const dest = safeNext(fromState) ?? "/";
      void navigate(dest, { replace: true });
    },
    onError: (err) => {
      if (err.code === "unlock_rate_limited" || err.code === "rate_limited") {
        setLockoutRemaining(err.retryAfter ?? RATE_LIMIT_FALLBACK_SECONDS);
        setPassphrase("");
        return;
      }
      if (err.code === "unlock_failed" || err.code === "unauthorized") {
        setSubmitError(t("unlock.unlockFailed"));
        setPassphrase("");
        inputRef.current?.focus();
        return;
      }
      if (err.code === "network_error") {
        setSubmitError(t("common.networkError"));
        return;
      }
      setSubmitError(t("common.unexpectedError"));
    },
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (lockoutRemaining !== null) return;
    if (passphrase.length === 0) return;
    setSubmitError(null);
    void mutation.mutateAsync({ passphrase });
  };

  if (lockoutRemaining !== null) {
    return (
      <AuthLayout>
        <Banner variant="warn" title={t("unlock.rateLimitedTitle")}>
          {t("unlock.rateLimitedBody", { seconds: lockoutRemaining })}
        </Banner>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <Banner variant="info" title={t("unlock.title")} className="mb-(--space-4)">
        {t("unlock.intro")}
      </Banner>
      <form onSubmit={handleSubmit} noValidate>
        <div className="mb-(--space-4)">
          <label htmlFor="unlock-passphrase" className="sr-only">
            {t("unlock.passphraseLabel")}
          </label>
          <Input
            ref={inputRef}
            id="unlock-passphrase"
            type="password"
            name="passphrase"
            size="lg"
            mono
            value={passphrase}
            onChange={(e) => setPassphrase(e.currentTarget.value)}
            autoComplete="off"
            placeholder={t("unlock.passphraseLabel")}
            required
            aria-required="true"
            invalid={submitError !== null}
            disabled={mutation.isPending}
          />
          {submitError !== null && (
            <p
              className="mt-(--space-2) text-(length:--text-sm) text-(--color-error)"
              role="alert"
            >
              {submitError}
            </p>
          )}
        </div>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          loading={mutation.isPending}
          className="w-full"
        >
          {mutation.isPending ? t("unlock.unlocking") : t("unlock.unlockButton")}
        </Button>
      </form>
    </AuthLayout>
  );
}
