import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SoundSensitivity } from '../types/socket';
import type { TriggerStatus } from '../types/shot';
import { DebugPanel } from './DebugPanel';

const noop = () => {};

const soundSensitivity: SoundSensitivity = {
  enabled: true,
  position: 64,
  max_position: 127,
  default_position: 127,
  sensitivity_percent: 50.4,
  resistance_ohms: 38039,
  preamp_feedback_ohms: 27557,
  series_ohms: 33000,
  simulated: false,
  auto_available: false,
  auto_enabled: false,
  last_peak: null,
  last_decision: null,
  error: null,
};

const triggerStatus: TriggerStatus = {
  mode: 'rolling-buffer',
  trigger_type: 'sound',
  radar_connected: true,
  radar_port: '/dev/ttyACM0',
  triggers_total: 0,
  triggers_accepted: 0,
  triggers_rejected: 0,
};

function render(status: TriggerStatus = triggerStatus) {
  return renderToString(
    <DebugPanel
      enabled
      readings={[]}
      shotLogs={[]}
      radarConfig={{ min_speed: 10, max_speed: 220, min_magnitude: 0, transmit_power: 0 }}
      cameraStatus={{
        available: false,
        enabled: false,
        streaming: false,
        ball_detected: false,
        ball_confidence: 0,
      }}
      mockMode={false}
      onToggle={noop}
      onUpdateConfig={noop}
      triggerDiagnostics={[]}
      triggerStatus={status}
      soundSensitivity={soundSensitivity}
      soundSensitivityError={null}
      onUpdateSoundSensitivity={noop}
      onToggleSoundSensitivityAuto={noop}
    />
  );
}

describe('DebugPanel tabs', () => {
  it('offers radar and sound tuning as separate tabs', () => {
    const html = render();

    expect(html).toContain('>Radar<');
    expect(html).toContain('>Sound<');
  });

  it('no longer offers the combined Tuning tab', () => {
    expect(render()).not.toContain('>Tuning<');
  });

  it('keeps status and history alongside them', () => {
    const html = render();

    expect(html).toContain('>Status<');
    expect(html).toContain('>History<');
  });

  it('hides history outside rolling-buffer mode but keeps both tuning tabs', () => {
    // Trigger history is rolling-buffer only; radar and sound tuning are not.
    const html = render({ ...triggerStatus, mode: 'swing-speed' });

    expect(html).not.toContain('>History<');
    expect(html).toContain('>Radar<');
    expect(html).toContain('>Sound<');
  });

  it('opens on the status tab, so neither tuning panel renders first', () => {
    const html = render();

    expect(html).not.toContain('Radar Tuning');
    expect(html).not.toContain('Sound Trigger Sensitivity');
  });
});
