import { z } from "zod";

/**
 * The backend serialises tz-aware datetimes as ISO-8601 with explicit
 * offset (Pydantic `AwareDatetime`). We validate the string shape and
 * leave conversion to `new Date(s)` at the call site — keeps the
 * raw value round-trippable without a coerce-and-stringify dance.
 */
export const AwareDatetime = z.string().datetime({ offset: true });
export type AwareDatetimeT = z.infer<typeof AwareDatetime>;

/**
 * Shape that backends return for a non-2xx response. `detail` may be a
 * single error-code string (the normal shape per `errors.py`) or a
 * Pydantic validation list (422 only). Both are accepted; apiFetch
 * picks the discriminator.
 */
export const ApiErrorBody = z
  .object({
    detail: z.union([z.string(), z.array(z.unknown())]),
  })
  .strict();
export type ApiErrorBodyT = z.infer<typeof ApiErrorBody>;
