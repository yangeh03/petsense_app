export type DogMapFocus = { index: number; request: number };

export type DogMapSurfaceProps = {
  html: string;
  resetRequest: number;
  focus: DogMapFocus | null;
  onMessage: (data: string) => void;
  onError: () => void;
};
