/** How often the Sound tab asks for a fresh envelope sample. */
export const ENVELOPE_POLL_MS = 100;

/** Poll `refresh` at 10 Hz until the returned stop function is called. */
export function startEnvelopePoll(refresh: () => void, intervalMs = ENVELOPE_POLL_MS): () => void {
  const id = setInterval(refresh, intervalMs);
  return () => clearInterval(id);
}
