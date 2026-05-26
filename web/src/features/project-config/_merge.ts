export function mergeKeepDirty<T>(current: T, next: T, dirty: unknown): T {
  if (dirty === true) return current;
  if (next === null || typeof next !== "object" || Array.isArray(next)) return next;
  if (current === null || typeof current !== "object" || Array.isArray(current)) return next;

  const out: Record<string, unknown> = { ...(current as Record<string, unknown>) };
  const nextObj = next as Record<string, unknown>;
  const dirtyObj = (dirty as Record<string, unknown> | null) ?? {};

  for (const key of Object.keys(nextObj)) {
    out[key] = mergeKeepDirty(
      (current as Record<string, unknown>)[key],
      nextObj[key],
      dirtyObj[key],
    );
  }
  return out as T;
}
