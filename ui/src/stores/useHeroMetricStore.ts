import { create } from 'zustand';

const STORAGE_KEY = 'openflight.hero-metric';

/**
 * Which metric the Live panel promotes into its hero slot (design doc 6a: the
 * metric tiles are buttons and tapping one swaps it with the hero). Persisted so
 * a player who cares about club speed keeps that choice across restarts.
 *
 * Stored as a plain metric id; an id that no longer exists is handled by
 * `splitHeroMetric`, which falls back to the first metric of the current set.
 */
function readStoredHeroMetric(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

interface HeroMetricState {
  heroMetricId: string | null;
  setHeroMetricId: (id: string) => void;
}

export const useHeroMetricStore = create<HeroMetricState>((set) => ({
  heroMetricId: readStoredHeroMetric(),
  setHeroMetricId: (heroMetricId) => {
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(STORAGE_KEY, heroMetricId);
      } catch {
        // Storage can be unavailable (private mode, quota); the choice still
        // applies for this session.
      }
    }

    set({ heroMetricId });
  },
}));
