import { useEffect, useState } from 'react';
import type { CameraStatus } from '../../stores/useCameraStore';
import { getServerOrigin } from '../../utils/serverOrigin';
import { PanelHeader } from './PanelHeader';

interface CameraPanelProps {
  cameraStatus: CameraStatus;
  clubLabel?: string;
  onToggleCamera: () => void;
  onToggleStream: () => void;
}

const STREAM_URL = `${getServerOrigin()}/camera/stream`;

/** The hairline camera glyph drawn in 7c's empty state. */
function CameraGlyph() {
  return (
    <span className="camera-panel__glyph" aria-hidden="true">
      <span className="camera-panel__glyph-lens" />
    </span>
  );
}

/**
 * Design doc 7c draws the disabled state. The unavailable, idle, streaming and
 * error states reuse the same hatched stage so the panel reads as one surface.
 */
export function CameraPanel({ cameraStatus, clubLabel, onToggleCamera, onToggleStream }: CameraPanelProps) {
  const [streamError, setStreamError] = useState(false);
  const { available, enabled, streaming, ball_detected, ball_confidence } = cameraStatus;

  useEffect(() => {
    if (streaming) {
      setStreamError(false);
    }
  }, [streaming]);

  const subtitle = !available
    ? 'Camera not connected'
    : !enabled
      ? 'Ball detection off'
      : ball_detected
        ? `Ball detected ${Math.round(ball_confidence * 100)}%`
        : 'Ball detection on';

  const stage = () => {
    if (!available) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">Camera unavailable</span>
          <span className="camera-panel__detail">Start the server with --camera to enable ball detection</span>
        </div>
      );
    }

    if (!enabled) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">Camera disabled</span>
          <span className="camera-panel__detail">Enable the camera to start ball detection</span>
        </div>
      );
    }

    if (!streaming) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">Stream paused</span>
          <span className="camera-panel__detail">
            Ball detection is running. Start the stream to see the live feed.
          </span>
        </div>
      );
    }

    if (streamError) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">Stream error</span>
          <span className="camera-panel__detail">Could not load the camera stream</span>
          <button type="button" className="panel-chip" onClick={() => setStreamError(false)}>
            Retry
          </button>
        </div>
      );
    }

    return (
      <div className="camera-panel__stage camera-panel__stage--live">
        <img src={STREAM_URL} alt="Camera feed" className="camera-panel__video" onError={() => setStreamError(true)} />
        <span className={`camera-panel__chip${ball_detected ? ' camera-panel__chip--detected' : ''}`}>
          {ball_detected ? `Ball ${Math.round(ball_confidence * 100)}%` : 'Searching…'}
        </span>
      </div>
    );
  };

  return (
    <div className="panel">
      <PanelHeader
        title="Camera"
        subtitle={subtitle}
        club={clubLabel}
        actions={
          available ? (
            <>
              <button type="button" className="panel-chip panel-chip--accent" onClick={onToggleCamera}>
                {enabled ? 'Disable camera' : 'Enable camera'}
              </button>
              {enabled ? (
                <button type="button" className="panel-chip" onClick={onToggleStream}>
                  {streaming ? 'Stop stream' : 'Start stream'}
                </button>
              ) : null}
            </>
          ) : null
        }
      />
      <div className="panel__body camera-panel__body">{stage()}</div>
    </div>
  );
}
