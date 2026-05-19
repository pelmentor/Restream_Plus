import { z } from "zod";

import { AwareDatetime } from "./_shared";

export const UserSummary = z.object({
  id: z.string(),
  username: z.string(),
  last_login_at: AwareDatetime.nullable(),
});
export type UserSummaryT = z.infer<typeof UserSummary>;

export const LoginRequest = z.object({
  username: z.string().min(1).max(128),
  password: z.string().min(1).max(1024),
});
export type LoginRequestT = z.infer<typeof LoginRequest>;

export const LoginResponse = z.object({
  user: UserSummary,
});
export type LoginResponseT = z.infer<typeof LoginResponse>;

export const UnlockRequest = z.object({
  passphrase: z.string().min(1).max(4096),
});
export type UnlockRequestT = z.infer<typeof UnlockRequest>;

export const UnlockResponse = z.object({
  state: z.literal("ready"),
});
export type UnlockResponseT = z.infer<typeof UnlockResponse>;

// ---------- Phase 9 — reprompt + change-password + rotate-passphrase ----------

export const RepromptScope = z.enum([
  "reveal_stream_key",
  "reveal_ingest_key",
  "regenerate_ingest_key",
  "rotate_passphrase",
  "change_password",
  "delete_target",
  "revoke_api_token",
  "clear_credential",
  "reset_target_worker",
]);
export type RepromptScopeT = z.infer<typeof RepromptScope>;

export const RepromptIssueRequest = z.object({
  password: z.string().min(1).max(1024),
  scope: RepromptScope,
});
export type RepromptIssueRequestT = z.infer<typeof RepromptIssueRequest>;

export const RepromptGrantResponse = z.object({
  grant_id: z.string(),
  expires_at: AwareDatetime,
});
export type RepromptGrantResponseT = z.infer<typeof RepromptGrantResponse>;

export const ChangePasswordRequest = z
  .object({
    current_password: z.string().min(1).max(1024),
    new_password: z.string().min(12).max(1024),
    confirm_new_password: z.string().min(1).max(1024),
  })
  .refine((d) => d.new_password === d.confirm_new_password, {
    path: ["confirm_new_password"],
    message: "passwords_do_not_match",
  });
export type ChangePasswordRequestT = z.infer<typeof ChangePasswordRequest>;

export const RotatePassphraseRequest = z
  .object({
    old_passphrase: z.string().min(1).max(4096),
    new_passphrase: z.string().min(12).max(4096),
    confirm_new_passphrase: z.string().min(1).max(4096),
  })
  .refine((d) => d.new_passphrase === d.confirm_new_passphrase, {
    path: ["confirm_new_passphrase"],
    message: "passphrases_do_not_match",
  });
export type RotatePassphraseRequestT = z.infer<typeof RotatePassphraseRequest>;
