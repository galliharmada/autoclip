/**
 * `crypto.randomUUID` is a secure-context-only API: browsers throw
 * "crypto.randomUUID is not a function" when the app is served over plain
 * HTTP from a non-localhost address (e.g. a Tailscale IP). Fall back to
 * `Math.random` so connection/draft IDs still get created.
 */
export function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}
