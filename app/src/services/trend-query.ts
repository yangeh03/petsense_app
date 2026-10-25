import type { TrendsResponse } from '../types/social';
type State<T> = { key: string; data: T | null; loading: boolean; error: string };
/** Reject late responses across identity/range changes and logout. */
export class TrendQuery<T = TrendsResponse> {
  private sequence = 0;
  private state: State<T> = { key: '', data: null, loading: false, error: '' };
  private listeners = new Set<() => void>();
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(state: State<T>) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }
  reset(key: string) {
    this.sequence++;
    this.publish({ key, data: null, loading: false, error: '' });
  }
  cancel() {
    this.sequence++;
    if (this.state.loading) this.publish({ ...this.state, loading: false });
  }
  async refresh(key: string, fetcher: () => Promise<T>) {
    const sequence = ++this.sequence;
    const data = this.state.key === key ? this.state.data : null;
    this.publish({ key, data, loading: true, error: '' });
    try {
      const result = await fetcher();
      if (sequence === this.sequence)
        this.publish({ key, data: result, loading: false, error: '' });
    } catch (error) {
      if (sequence === this.sequence)
        this.publish({
          key,
          data,
          loading: false,
          error: error instanceof Error ? error.message : '趋势加载失败，请重试',
        });
    }
  }
}
