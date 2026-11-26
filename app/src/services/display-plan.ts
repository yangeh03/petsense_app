export type DisplayPlan = {
  deviceId: string;
  planId: string;
  revision: string;
  name: string;
  image: { width: number; height: number; imageDataUri: string };
};

export function displayPlan(value: unknown, deviceId: string): DisplayPlan | null {
  if (!value || typeof value !== 'object') return null;
  const p = value as Record<string, unknown>;
  if (
    p.deviceId !== deviceId ||
    p.schemaVersion !== 1 ||
    p.units !== 'relative' ||
    p.calibrationStatus !== 'required'
  )
    return null;
  if (typeof p.planId !== 'string' || typeof p.revision !== 'string' || typeof p.name !== 'string')
    return null;
  const bg = p.image as Record<string, unknown> | undefined;
  if (
    !bg ||
    !Number.isInteger(bg.width) ||
    !Number.isInteger(bg.height) ||
    Number(bg.width) < 1 ||
    Number(bg.height) < 1 ||
    Number(bg.width) * Number(bg.height) > 20000000 ||
    typeof bg.imageDataUri !== 'string' ||
    !bg.imageDataUri.startsWith('data:image/png;base64,') ||
    bg.imageDataUri.length > 5500000
  )
    return null;
  return {
    deviceId,
    planId: p.planId,
    revision: p.revision,
    name: p.name,
    image: { width: Number(bg.width), height: Number(bg.height), imageDataUri: bg.imageDataUri },
  };
}
