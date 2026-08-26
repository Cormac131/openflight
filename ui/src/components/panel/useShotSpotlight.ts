import { useEffect, useRef, useState } from 'react';
import type { LiveViewMode } from '../../stores/useLiveViewStore';

export function shouldOpenSpotlight(mode: LiveViewMode, isNewShot: boolean): boolean {
  return isNewShot && mode !== 'tiles';
}

export function createSpotlightController(
  mode: LiveViewMode,
  durationMs: number,
  isNewShot: boolean,
  hide: () => void
): { openInitially: boolean; start: () => () => void } {
  const openInitially = shouldOpenSpotlight(mode, isNewShot);
  return {
    openInitially,
    start: () => {
      if (!openInitially || mode !== 'timed') {
        return () => {};
      }
      const timer = setTimeout(hide, durationMs);
      return () => clearTimeout(timer);
    },
  };
}

export function useShotSpotlight(mode: LiveViewMode, durationMs: number, isNewShot: boolean) {
  const hideRef = useRef(() => {});
  const controllerRef = useRef<ReturnType<typeof createSpotlightController> | null>(null);
  if (controllerRef.current === null) {
    // Freeze mode/duration for this hook instance. LivePanel remounts on a new
    // shot, so menu changes apply then; an overlay already on screen is left alone.
    controllerRef.current = createSpotlightController(mode, durationMs, isNewShot, () => hideRef.current());
  }
  const [open, setOpen] = useState(controllerRef.current.openInitially);
  hideRef.current = () => setOpen(false);

  useEffect(() => {
    return controllerRef.current?.start();
  }, []);

  return { open, dismiss: () => setOpen(false) };
}
