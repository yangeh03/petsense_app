import type { UwbMap } from '@/types/uwb';

export type AlignmentCandidate = {
  id: 'normal' | 'mirror' | 'grid' | 'manual';
  eligible: boolean;
  report: {
    boundary_coverage_ratio: number;
    median_edge_error_m: number | null;
    inlier_ratio: number;
    scale?: number;
    grid?: { in_grid_ratio: number; inside_indoor_ratio: number; p90_boundary_distance_m: number };
  };
  trail: [number, number][];
};

export type UwbSetupSession = {
  id: string;
  tagId: number;
  phase:
    | 'touch_ready'
    | 'touching'
    | 'anchors_ready'
    | 'walking'
    | 'walk_ready'
    | 'processing'
    | 'alignment_review'
    | 'published';
  message: string;
  rows: (number[] | null)[];
  anchors: number[][] | null;
  capture: { anchor: number; endsAt: number; samples: number } | null;
  jobId?: string;
  walk?: {
    id: string;
    points: number;
    rejected: number;
    elapsedMs: number;
    lengthM: number;
    closureM: number | null;
    ageMs: number | null;
    trail: [number, number][];
  } | null;
  inputs?: Record<
    'pointcloud' | 'floor' | 'alignment',
    { present: boolean; path: string; url: string }
  >;
  alignment?: { recommended: string; candidates: AlignmentCandidate[] } | null;
};

export type AlignmentPreview = { jobId: string; map: UwbMap; candidate: AlignmentCandidate };

/** Prevent a late preview response or another job from enabling publication. */
export function canPublishPreview(
  preview: AlignmentPreview | null,
  jobId: string,
  candidate: string,
) {
  return (
    !!preview &&
    preview.jobId === jobId &&
    preview.candidate.id === candidate &&
    preview.candidate.eligible
  );
}
