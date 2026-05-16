import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { DestructiveConfirm } from "@/components/DestructiveConfirm";
import { OneTimeRevealBanner } from "@/components/OneTimeRevealBanner";
import { TypeToConfirmDialog } from "@/components/TypeToConfirmDialog";
import { SettingsSection } from "@/components/settings/SettingsSection";
import { useAuthReprompt, REPROMPT_CANCELLED } from "@/hooks/useAuthReprompt";
import {
  useApiTokens,
  useCreateApiToken,
  useRevokeApiToken,
} from "@/hooks/useApiTokens";
import {
  useHttpSessions,
  useRevokeHttpSession,
} from "@/hooks/useHttpSessions";
import { apiFetch, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { ApiTokenCreatedResponseT } from "@/lib/schemas/apiTokens";
import { t } from "@/messages";

export function SecurityTab(): ReactNode {
  return (
    <div>
      <h1 className="mb-(--space-2) text-(length:--text-2xl) font-semibold text-(--color-fg-strong)">
        {t("settings.pageSecurityTitle")}
      </h1>
      <p className="mb-(--space-6) text-(length:--text-sm) text-(--color-fg-muted)">
        {t("settings.pageSecuritySubtitle")}
      </p>
      <ChangePasswordSection />
      <RotatePassphraseSection />
      <ApiTokensSection />
      <HttpSessionsSection />
      <LogoutEverywhereSection />
    </div>
  );
}

// ----------------- Change password -----------------

function ChangePasswordSection(): ReactNode {
  const reprompt = useAuthReprompt();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const form = useForm<{
    current_password: string;
    new_password: string;
    confirm_new_password: string;
  }>({
    defaultValues: {
      current_password: "",
      new_password: "",
      confirm_new_password: "",
    },
  });
  const [error, setError] = useState<string>("");

  const onSubmit = form.handleSubmit(async (vals) => {
    if (vals.new_password !== vals.confirm_new_password) {
      form.setError("confirm_new_password", { message: "Passwords differ." });
      return;
    }
    setError("");
    try {
      const grantId = await reprompt("change_password");
      await apiFetch("auth/change-password", {
        method: "POST",
        json: {
          current_password: vals.current_password,
          new_password: vals.new_password,
        },
        headers: { "X-Reprompt-Grant": grantId },
        silenceGlobalErrors: true,
      });
      queryClient.clear();
      void navigate("/login", { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
      const err = e as ApiError;
      // Match on err.code (reviewer M-3): `silenceGlobalErrors:true`
      // means a session-expired 401 (`unauthorized`) lands here too,
      // and we should NOT surface it as "wrong password" — instead let
      // the global flow handle it.
      if (err.code === "invalid_credentials") {
        setError(t("login.invalidCredentials"));
      } else if (err.status === 401) {
        queryClient.clear();
        void navigate("/login", { replace: true });
      } else {
        setError(t("common.unexpectedError"));
      }
    }
  });

  return (
    <form onSubmit={onSubmit}>
      <SettingsSection
        title={t("security.changePasswordSection")}
        intro={t("security.changePasswordWarning")}
        footer={
          <button
            type="submit"
            disabled={form.formState.isSubmitting}
            className={cn(
              "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
              "bg-(--color-accent) hover:bg-(--color-accent-strong)",
              form.formState.isSubmitting && "opacity-50 cursor-not-allowed",
            )}
          >
            {form.formState.isSubmitting ? t("settings.saving") : t("security.changePassword")}
          </button>
        }
      >
        <PasswordField
          label={t("security.currentPasswordLabel")}
          {...form.register("current_password", { required: true })}
          autoComplete="current-password"
        />
        <PasswordField
          label={t("security.newPasswordLabel")}
          {...form.register("new_password", { required: true, minLength: 10 })}
          autoComplete="new-password"
        />
        <PasswordField
          label={t("security.confirmNewPasswordLabel")}
          {...form.register("confirm_new_password", { required: true })}
          autoComplete="new-password"
          error={form.formState.errors.confirm_new_password?.message}
        />
        {error && (
          <p className="text-(length:--text-xs) text-(--color-error)">
            {error}
          </p>
        )}
      </SettingsSection>
    </form>
  );
}

// ----------------- Rotate master passphrase -----------------

function RotatePassphraseSection(): ReactNode {
  const reprompt = useAuthReprompt();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const form = useForm<{
    old_passphrase: string;
    new_passphrase: string;
    confirm_new_passphrase: string;
  }>({
    defaultValues: {
      old_passphrase: "",
      new_passphrase: "",
      confirm_new_passphrase: "",
    },
  });
  const [error, setError] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = form.handleSubmit(async (vals) => {
    if (vals.new_passphrase !== vals.confirm_new_passphrase) {
      form.setError("confirm_new_passphrase", { message: "Passphrases differ." });
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const grantId = await reprompt("rotate_passphrase");
      await apiFetch("security/rotate-passphrase", {
        method: "POST",
        json: {
          old_passphrase: vals.old_passphrase,
          new_passphrase: vals.new_passphrase,
        },
        headers: { "X-Reprompt-Grant": grantId },
        silenceGlobalErrors: true,
      });
      queryClient.clear();
      void navigate("/login", { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) {
        setSubmitting(false);
        return;
      }
      const err = e as ApiError;
      if (err.code === "invalid_credentials") {
        setError(t("login.invalidCredentials"));
      } else if (err.code === "same_passphrase") {
        form.setError("new_passphrase", { message: "Same as old." });
      } else if (err.code === "run_active") {
        setError(
          "Stop the active stream before rotating the master passphrase.",
        );
      } else {
        setError(t("common.unexpectedError"));
      }
      setSubmitting(false);
    }
  });

  return (
    <form onSubmit={onSubmit}>
      <SettingsSection
        title={t("security.rotatePassphraseSection")}
        intro={t("security.rotatePassphraseWarning")}
        footer={
          <button
            type="submit"
            disabled={submitting}
            className={cn(
              "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
              "bg-(--color-error) hover:bg-(--color-error)/85",
              submitting && "opacity-50 cursor-not-allowed",
            )}
          >
            {submitting ? t("security.rotating") : t("security.rotatePassphrase")}
          </button>
        }
      >
        <PasswordField
          label={t("security.currentPassphraseLabel")}
          {...form.register("old_passphrase", { required: true })}
          autoComplete="current-password"
        />
        <PasswordField
          label={t("security.newPassphraseLabel")}
          {...form.register("new_passphrase", { required: true, minLength: 12 })}
          autoComplete="new-password"
          error={form.formState.errors.new_passphrase?.message}
        />
        <PasswordField
          label={t("security.confirmNewPassphraseLabel")}
          {...form.register("confirm_new_passphrase", { required: true })}
          autoComplete="new-password"
          error={form.formState.errors.confirm_new_passphrase?.message}
        />
        {error && (
          <p className="text-(length:--text-xs) text-(--color-error)">
            {error}
          </p>
        )}
      </SettingsSection>
    </form>
  );
}

function PasswordField({
  label,
  error,
  ...rest
}: {
  readonly label: string;
  readonly error?: string | undefined;
} & React.InputHTMLAttributes<HTMLInputElement>): ReactNode {
  const id = `pw-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <div>
      <label
        htmlFor={id}
        className="block text-(length:--text-sm) font-medium text-(--color-fg-strong)"
      >
        {label}
      </label>
      <input
        id={id}
        type="password"
        spellCheck={false}
        {...rest}
        className={cn(
          "mt-(--space-1) h-10 w-full rounded-(--radius-md) border bg-(--color-bg-base)",
          "border-(--color-border-subtle) px-(--space-3)",
          "text-(length:--text-sm) text-(--color-fg-strong)",
          "focus:border-(--color-accent) focus:outline-none",
          error !== undefined && error !== "" && "border-(--color-error)",
        )}
      />
      {error !== undefined && error !== "" && (
        <p className="mt-(--space-1) text-(length:--text-xs) text-(--color-error)">{error}</p>
      )}
    </div>
  );
}

// ----------------- API tokens -----------------

function ApiTokensSection(): ReactNode {
  const tokens = useApiTokens();
  const create = useCreateApiToken();
  const revoke = useRevokeApiToken();
  const reprompt = useAuthReprompt();
  const [creating, setCreating] = useState(false);
  const [label, setLabel] = useState("");
  const [revealed, setRevealed] = useState<ApiTokenCreatedResponseT | null>(null);

  const onCreate = async (): Promise<void> => {
    if (!label.trim()) return;
    try {
      const r = await create.mutateAsync({ label: label.trim() });
      setRevealed(r);
      setLabel("");
    } catch {
      /* mutation isError shown inline */
    }
  };

  const onRevoke = async (id: string): Promise<void> => {
    try {
      const grantId = await reprompt("revoke_api_token");
      await revoke.mutateAsync({ id, grantId });
    } catch (e) {
      if (e instanceof ApiError && e.message === REPROMPT_CANCELLED) return;
    }
  };

  const rows = tokens.data ?? [];

  return (
    <SettingsSection title={t("security.apiTokensSection")}>
      {revealed !== null && (
        <OneTimeRevealBanner
          title={t("reveal.bannerTitle")}
          body={t("reveal.bannerBody")}
          value={revealed.plaintext}
          ariaLabel="API token"
          onDismiss={() => setRevealed(null)}
        />
      )}
      {creating ? (
        <div className="flex items-end gap-(--space-2)">
          <div className="flex-1">
            <label className="block text-(length:--text-sm) font-medium text-(--color-fg-strong)">
              {t("security.tokenLabelInput")}
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                className={cn(
                  "mt-(--space-1) h-10 w-full rounded-(--radius-md) border bg-(--color-bg-base)",
                  "border-(--color-border-subtle) px-(--space-3)",
                  "text-(length:--text-sm) text-(--color-fg-strong)",
                  "focus:border-(--color-accent) focus:outline-none",
                )}
              />
            </label>
          </div>
          <button
            type="button"
            onClick={() => void onCreate()}
            disabled={!label.trim() || create.isPending}
            className={cn(
              "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
              "bg-(--color-accent) hover:bg-(--color-accent-strong)",
              (!label.trim() || create.isPending) && "opacity-50 cursor-not-allowed",
            )}
          >
            {t("security.create")}
          </button>
          <button
            type="button"
            onClick={() => {
              setCreating(false);
              setLabel("");
            }}
            className={cn(
              "h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm)",
              "text-(--color-fg-default) hover:bg-(--color-bg-sunken)",
            )}
          >
            {t("common.cancel")}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setCreating(true)}
          className={cn(
            "self-start h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium",
            "border border-(--color-accent) text-(--color-accent) hover:bg-(--color-accent-faint)",
          )}
        >
          {t("security.newToken")}
        </button>
      )}
      {rows.length === 0 ? (
        <div className="rounded-(--radius-md) border border-dashed border-(--color-border-subtle) p-(--space-4) text-center">
          <p className="text-(length:--text-sm) text-(--color-fg-muted)">
            {t("security.apiTokensEmpty")}
          </p>
          <p className="mt-(--space-1) text-(length:--text-xs) text-(--color-fg-muted)">
            {t("security.apiTokensEmptyHelper")}
          </p>
        </div>
      ) : (
        <table className="w-full text-(length:--text-sm)">
          <thead className="border-b border-(--color-border-subtle) text-(--color-fg-muted)">
            <tr>
              <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">{t("security.tokenLabelInput")}</th>
              <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">{t("security.apiTokenColCreated")}</th>
              <th scope="col" className="px-(--space-2) py-(--space-2) text-left font-medium">{t("security.apiTokenColLastUsed")}</th>
              <th scope="col" className="px-(--space-2) py-(--space-2)" />
            </tr>
          </thead>
          <tbody>
            {rows.map((tok) => (
              <tr key={tok.id} className="border-b border-(--color-border-subtle)">
                <td className="px-(--space-2) py-(--space-2) text-(--color-fg-strong)">{tok.label}</td>
                <td className="px-(--space-2) py-(--space-2) text-(--color-fg-muted) tabular-nums">
                  {new Date(tok.created_at).toLocaleDateString()}
                </td>
                <td className="px-(--space-2) py-(--space-2) text-(--color-fg-muted) tabular-nums">
                  {tok.last_used_at !== null ? new Date(tok.last_used_at).toLocaleDateString() : "—"}
                </td>
                <td className="px-(--space-2) py-(--space-2) text-right">
                  <DestructiveConfirm
                    trigger={
                      <button
                        type="button"
                        className="h-8 rounded-(--radius-md) px-(--space-2) text-(length:--text-sm) text-(--color-error) hover:bg-(--color-error-faint)"
                      >
                        {t("security.revoke")}
                      </button>
                    }
                    body={t("security.revokeConfirm")}
                    confirmLabel={t("security.revoke")}
                    onConfirm={() => void onRevoke(tok.id)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SettingsSection>
  );
}

// ----------------- HTTP sessions -----------------

function HttpSessionsSection(): ReactNode {
  const sessions = useHttpSessions();
  const revoke = useRevokeHttpSession();
  const rows = sessions.data ?? [];
  return (
    <SettingsSection title={t("security.httpSessionsSection")}>
      {rows.length === 0 ? (
        <p className="text-(length:--text-sm) text-(--color-fg-muted)">
          {t("security.httpSessionsEmpty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-(--space-2)">
          {rows.map((s) => (
            <li
              key={s.id_fingerprint}
              className="flex items-center gap-(--space-3) rounded-(--radius-md) border border-(--color-border-subtle) px-(--space-3) py-(--space-2)"
            >
              <div className="flex-1 min-w-0">
                <p className="truncate text-(length:--text-sm) text-(--color-fg-strong)">
                  {s.user_agent ?? "Unknown device"}
                  {s.is_current && (
                    <span className="ml-(--space-2) rounded-(--radius-full) bg-(--color-accent-faint) px-(--space-2) py-0.5 text-(length:--text-2xs) text-(--color-accent)">
                      {t("security.httpSessionsCurrent")}
                    </span>
                  )}
                </p>
                <p className="text-(length:--text-xs) text-(--color-fg-muted) tabular-nums">
                  {s.ip ?? "?"} · {new Date(s.last_seen_at).toLocaleString()}
                </p>
              </div>
              <DestructiveConfirm
                trigger={
                  <button
                    type="button"
                    className="h-8 rounded-(--radius-md) px-(--space-2) text-(length:--text-sm) text-(--color-error) hover:bg-(--color-error-faint)"
                  >
                    {t("security.revoke")}
                  </button>
                }
                body={t("security.revokeConfirm")}
                confirmLabel={t("security.revoke")}
                onConfirm={() =>
                  void revoke.mutateAsync({ fingerprint: s.id_fingerprint })
                }
              />
            </li>
          ))}
        </ul>
      )}
    </SettingsSection>
  );
}

// ----------------- Logout everywhere -----------------

function LogoutEverywhereSection(): ReactNode {
  const [confirm, setConfirm] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const onConfirm = async (): Promise<void> => {
    try {
      await apiFetch("auth/logout-all", { method: "POST", silenceGlobalErrors: true });
    } catch {
      /* still nav */
    }
    queryClient.clear();
    void navigate("/login", { replace: true });
  };
  return (
    <SettingsSection title={t("security.logoutAllSection")}>
      <button
        type="button"
        onClick={() => setConfirm(true)}
        className={cn(
          "self-start h-10 rounded-(--radius-md) px-(--space-4) text-(length:--text-sm) font-medium text-white",
          "bg-(--color-error) hover:bg-(--color-error)/85",
        )}
      >
        {t("security.logoutAllButton")}
      </button>
      <TypeToConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("security.logoutAllConfirmTitle")}
        body={t("security.logoutAllConfirmBody")}
        phrase={t("security.logoutAllPhrase")}
        confirmLabel={t("security.logoutAllButton")}
        onConfirm={() => void onConfirm()}
      />
    </SettingsSection>
  );
}
