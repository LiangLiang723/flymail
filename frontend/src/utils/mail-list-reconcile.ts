export function reconcileMessagePage<T extends { id: string }>(
  current: readonly T[],
  incoming: readonly T[],
): T[] {
  const currentById = new Map(current.map((message) => [message.id, message]));

  return incoming.map((message) => {
    const existing = currentById.get(message.id);
    return existing ? { ...existing, ...message } : { ...message };
  });
}
