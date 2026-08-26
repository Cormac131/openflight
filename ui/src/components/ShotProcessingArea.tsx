import type { ReactNode } from 'react';
import type { ShotProcessingPhase } from '../stores/useShotStore';
import { ProgressIndicator } from './ProgressIndicator';
import './ShotProcessingArea.css';

interface ShotProcessingAreaProps {
  phase: ShotProcessingPhase | null;
  children: ReactNode;
}

export function ShotProcessingArea({ phase, children }: ShotProcessingAreaProps) {
  if (phase === 'iwr_dump') {
    return (
      <>
        <div className="shot-processing-status">
          <ProgressIndicator variant="inline" title="OPS metrics ready" detail="Receiving IWR radar dump…" />
        </div>
        {children}
      </>
    );
  }

  return (
    <>
      {phase ? (
        <div className="shot-processing-overlay">
          <div className="shot-processing-card">
            <ProgressIndicator
              variant="dialog"
              title={phase === 'capturing' ? 'Impact detected' : 'Shot captured'}
              detail={phase === 'capturing' ? 'Capturing radar data…' : 'Calculating metrics…'}
            />
          </div>
        </div>
      ) : null}
      {children}
    </>
  );
}
