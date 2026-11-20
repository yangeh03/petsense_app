export type DogMapFocus = { index: number; request: number };

export type DogMapSurfaceProps = {
  html: string;
  resetRequest: number;
  focus: DogMapFocus | null;
  /** 隐藏中的 WebView 可能吞掉注入；重新可见时补发最后一次聚焦。 */
  visible: boolean;
  onMessage: (data: string) => void;
  onError: () => void;
};
