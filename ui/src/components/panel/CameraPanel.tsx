import { useEffect, useState } from 'react';
import type { CameraStatus } from '../../stores/useCameraStore';
import { getServerOrigin } from '../../utils/serverOrigin';
import { PanelHeader } from './PanelHeader';
import { useI18n } from '../../i18n/useI18n';

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
  const { t } = useI18n();
  const [streamError, setStreamError] = useState(false);
  const { available, enabled, streaming, ball_detected, ball_confidence } = cameraStatus;

  useEffect(() => {
    if (streaming) {
      setStreamError(false);
    }
  }, [streaming]);

  const subtitle = !available
    ? t('camera.notConnected')
    : !enabled
      ? t('camera.detectionOff')
      : ball_detected
        ? t('camera.detected', { percent: Math.round(ball_confidence * 100) })
        : t('camera.detectionOn');

  const stage = () => {
    if (!available) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">{t('camera.unavailable')}</span>
          <span className="camera-panel__detail">{t('camera.unavailableDetail')}</span>
        </div>
      );
    }

    if (!enabled) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">{t('camera.disabled')}</span>
          <span className="camera-panel__detail">{t('camera.disabledDetail')}</span>
        </div>
      );
    }

    if (!streaming) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">{t('camera.streamPaused')}</span>
          <span className="camera-panel__detail">{t('camera.streamPausedDetail')}</span>
        </div>
      );
    }

    if (streamError) {
      return (
        <div className="camera-panel__stage">
          <CameraGlyph />
          <span className="camera-panel__title">{t('camera.streamError')}</span>
          <span className="camera-panel__detail">{t('camera.streamErrorDetail')}</span>
          <button type="button" className="panel-chip" onClick={() => setStreamError(false)}>
            {t('camera.retry')}
          </button>
        </div>
      );
    }

    return (
      <div className="camera-panel__stage camera-panel__stage--live">
        <img
          src={STREAM_URL}
          alt={t('camera.feedAlt')}
          className="camera-panel__video"
          onError={() => setStreamError(true)}
        />
        <span className={`camera-panel__chip${ball_detected ? ' camera-panel__chip--detected' : ''}`}>
          {ball_detected
            ? t('camera.ballPercent', { percent: Math.round(ball_confidence * 100) })
            : t('camera.searching')}
        </span>
      </div>
    );
  };

  return (
    <div className="panel">
      <PanelHeader
        title={t('nav.camera')}
        subtitle={subtitle}
        club={clubLabel}
        actions={
          available ? (
            <>
              <button type="button" className="panel-chip panel-chip--accent" onClick={onToggleCamera}>
                {enabled ? t('camera.disable') : t('camera.enable')}
              </button>
              {enabled ? (
                <button type="button" className="panel-chip" onClick={onToggleStream}>
                  {streaming ? t('camera.stopStream') : t('camera.startStream')}
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
