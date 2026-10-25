/** 只传递失效信号，不缓存任何账号或宠物资料。 */
const listeners = new Map<number, Set<() => void>>();

export function subscribePetChanges(userId: number, listener: () => void): () => void {
  const group = listeners.get(userId) ?? new Set<() => void>();
  group.add(listener);
  listeners.set(userId, group);
  return () => {
    group.delete(listener);
    if (!group.size) listeners.delete(userId);
  };
}

export function notifyPetChanged(userId: number): void {
  for (const listener of listeners.get(userId) ?? []) listener();
}
