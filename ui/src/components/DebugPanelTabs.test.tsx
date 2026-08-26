import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SoundSensitivity } from '../types/socket';
import type { TriggerStatus } from '../types/shot';
import { DebugPanel } from './DebugPanel';

const noop = () => {};

const soundSensitivity: SoundSensitivity = {
  enabled: true,
  position: 46,
  max_position: 99,
  default_position: 46,
  sensitivity_percent: 46.5,
  resistance_ohms: 46504,
  preamp_feedback_ohms: 31742,
  simulated: false,
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
      onRecalibrateSoundSensitivity={noop}
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
