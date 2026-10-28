export function storageSpace(bytes: number | null | undefined): string {
  return typeof bytes === 'number' && Number.isFinite(bytes) && bytes >= 0
    ? `${(bytes / 1048576).toFixed(1)} MiB`
    : '未上报';
}
