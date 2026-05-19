import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Banner } from "@/components/Banner";
import { Button } from "@/components/Button";
import { Input } from "@/components/Input";
import { AuthLayout } from "@/components/AuthLayout";
import { apiFetch, type ApiError } from "@/lib/api";
import { safeNext } from "@/lib/safeNext";
import { LoginResponse, type LoginResponseT } from "@/lib/schemas/auth";
import { t } from "@/messages";

const RATE_LIMIT_FALLBACK_SECONDS = 60;

export function LoginPage(): ReactNode {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const [password, setPassword] = useState("");
  const [capsLockOn, setCapsLockOn] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lockoutRemaining, setLockoutRemaining] = useState<number | null>(null);

  useEffect(() => {
    passwordRef.current?.focus();
    document.title = t("login.title");
  }, []);

  // Rate-limit countdown ticker. Stops on 0; the form re-mounts.
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

  const loginMutation = useMutation<LoginResponseT, ApiError, { username: string; password: string }>({
    // meta.silenceGlobalErrors — review M-1: prevents the MutationCache
    // from racing this mutation's inline handlers when login itself
    // returns 401/503.
    meta: { silenceGlobalErrors: true },
    mutationFn: (body) =>
      apiFetch(
        "auth/login",
        { method: "POST", json: body, silenceGlobalErrors: true },
        LoginResponse,
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(["auth", "session"], data.user);
      const fromState = (location.state as { from?: unknown } | null)?.from;
      const dest = safeNext(fromState) ?? "/";
      void navigate(dest, { replace: true });
    },
    onError: (err) => {
      if (err.code === "rate_limited") {
        setLockoutRemaining(err.retryAfter ?? RATE_LIMIT_FALLBACK_SECONDS);
        setPassword("");
        return;
      }
      if (err.code === "invalid_credentials" || err.code === "unauthorized") {
        setSubmitError(t("login.invalidCredentials"));
        setPassword("");
        passwordRef.current?.focus();
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
    if (password.length === 0) return;
    setSubmitError(null);
    void loginMutation.mutateAsync({ username: "admin", password });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    setCapsLockOn(event.getModifierState("CapsLock"));
  };

  if (lockoutRemaining !== null) {
    return (
      <AuthLayout>
        <Banner variant="warn" title={t("login.rateLimitedTitle")}>
          {t("login.rateLimitedBody", { seconds: lockoutRemaining })}
        </Banner>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <form onSubmit={handleSubmit} noValidate>
        {/* Hidden username for password managers. Load-bearing per
            phase-7-design-memo §G — without it, browser credential
            saving silently degrades. */}
        <input
          type="text"
          name="username"
          value="admin"
          autoComplete="username"
          readOnly
          hidden
          tabIndex={-1}
        />
        <div className="mb-(--space-4)">
          <label htmlFor="login-password" className="sr-only">
            {t("login.passwordLabel")}
          </label>
          <Input
            ref={passwordRef}
            id="login-password"
            type="password"
            name="password"
            size="lg"
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
            onKeyDown={handleKeyDown}
            autoComplete="current-password"
            placeholder={t("login.passwordPlaceholder")}
            required
            aria-required="true"
            invalid={submitError !== null}
            disabled={loginMutation.isPending}
          />
          {capsLockOn && (
            <p className="mt-(--space-1) text-(length:--text-xs) text-(--color-warn)" role="status">
              {t("login.capsLockOn")}
            </p>
          )}
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
          loading={loginMutation.isPending}
          className="w-full"
        >
          {loginMutation.isPending ? t("login.submitting") : t("common.signIn")}
        </Button>
      </form>
    </AuthLayout>
  );
}
