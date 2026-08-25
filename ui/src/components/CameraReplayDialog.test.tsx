import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CameraReplayDialog } from './CameraReplayDialog';

const replay = {
  id: 'replay-123',
  frame_count: 99,
  trigger_frame: 73,
  playback_fps: 60,
  duration_seconds: 1.65,
};

describe('CameraReplayDialog', () => {
  it('shows an explicit preparation state before the manually requested MP4 is ready', () => {
    const html = renderToString(
      <CameraReplayDialog replay={replay} state={{ kind: 'preparing' }} onClose={() => {}} onRetry={() => {}} />
    );

    expect(html).toContain('Preparing replay');
    expect(html).not.toContain('<video');
  });

  it('renders a touch player and positions the impact marker from capture metadata', () => {
    const html = renderToString(
      <CameraReplayDialog
        replay={replay}
        state={{ kind: 'ready', videoUrl: 'http://localhost/replay.mp4' }}
        onClose={() => {}}
        onRetry={() => {}}
      />
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('class="camera-replay__viewport"');
    expect(html).toContain('<video');
    expect(html).toContain('http://localhost/replay.mp4');
    expect(html).toContain('--replay-impact-position:74.48979591836735%');
    expect(html).toContain('aria-label="Impact"');
    expect(html).toContain('Replay from start');
  });

  it('offers retry and close after preparation fails', () => {
    const html = renderToString(
      <CameraReplayDialog replay={replay} state={{ kind: 'error' }} onClose={() => {}} onRetry={() => {}} />
    );

    expect(html).toContain('Could not prepare replay');
    expect(html).toContain('Try again');
    expect(html).toContain('Close replay');
  });

  it('gives playback failures their own retryable feedback', () => {
    const html = renderToString(
      <CameraReplayDialog
        replay={replay}
        state={{ kind: 'error', stage: 'playback' }}
        onClose={() => {}}
        onRetry={() => {}}
        onPlaybackError={() => {}}
      />
    );

    expect(html).toContain('Could not play replay');
    expect(html).toContain('Try again');
  });

  it('keeps every touch control at least 44 CSS pixels tall', () => {
    const css = readFileSync(fileURLToPath(new URL('./CameraReplayDialog.css', import.meta.url)), 'utf8');

    expect(css).toMatch(/\.camera-replay__button \{[^}]*min-height: 44px/);
    expect(css).toMatch(/\.camera-replay__scrubber \{[^}]*min-height: 44px/);
  });

  it('keeps the inset video in a dedicated row above every control', () => {
    const css = readFileSync(fileURLToPath(new URL('./CameraReplayDialog.css', import.meta.url)), 'utf8');

    expect(css).toMatch(
      /\.camera-replay__dialog \{[^}]*grid-template-areas:\s*['"]header['"]\s*['"]stage['"]\s*['"]controls['"]/s
    );
    expect(css).toMatch(/\.camera-replay__stage \{[^}]*grid-area: stage;[^}]*position: relative;/s);
    expect(css).toMatch(/\.camera-replay__viewport \{[^}]*position: absolute;[^}]*inset:/s);
    expect(css).toMatch(/\.camera-replay__controls \{[^}]*grid-area: controls;/s);
  });
});
