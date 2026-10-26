export type DiaryDraft = {
  durationMin: string;
  steps: string;
  places: string;
  notes: string;
};

export type ValidDiaryDraft = {
  durationMin: number;
  steps: number;
  places: string;
  notes: string;
};

export function validateDiaryDraft(draft: DiaryDraft): ValidDiaryDraft | null {
  if (!draft.steps.trim()) return null;
  const durationMin = Number(draft.durationMin);
  const steps = Number(draft.steps);
  if (
    !Number.isInteger(durationMin) ||
    durationMin < 1 ||
    durationMin > 600 ||
    !Number.isInteger(steps) ||
    steps < 0 ||
    steps > 100_000 ||
    draft.places.trim().length > 200 ||
    draft.notes.trim().length > 200
  ) {
    return null;
  }
  return {
    durationMin,
    steps,
    places: draft.places.trim(),
    notes: draft.notes.trim(),
  };
}
