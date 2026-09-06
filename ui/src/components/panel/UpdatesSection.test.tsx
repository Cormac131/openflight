import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { UpdateStatus } from '../../types/update';
import { UpdatesSection } from './UpdatesSection';

const noop = () => {};

const managed: UpdateStatus = {
  format_version: 1,
  managed: true,
  state: 'staged',
  channel: 'stable',
  repository: 'open-flight/openflight',
  current: { tag: 'v0.3.0', version: '0.3.0', channel: 'stable' },
  available: { tag: 'v0.3.1', version: '0.3.1', size: 6_500_000 },
  staged: { name: 'v0.3.1', tag: 'v0.3.1', version: '0.3.1', channel: 'stable' },
  previous: null,
  pending_confirm: false,
  last_check_at: '2026-09-06T08:00:00+00:00',
  error: null,
};

function render(status: UpdateStatus | null) {
  return renderToString(<UpdatesSection status={status} onSetChannel={noop} onCheck={noop} onRestartToUpdate={noop} />);
}

describe('UpdatesSection', () => {
  it('shows Unavailable with no controls before update_status arrives', () => {
    const html = render(null);

    expect(html).toContain('menu-sheet__section-title">Updates');
    expect(html).toContain('menu-sheet__status-value">Unavailable<');
    expect(html).not.toContain('aria-label="Release channel"');
    expect(html).not.toContain('<button');
  });

  it('offers the restart chip and the channel control once a release is staged', () => {
    const html = render(managed);

    expect(html).toContain('aria-label="Release channel"');
    expect(html).toContain('aria-pressed="true">Stable<');
    expect(html).toContain('v0.3.1 ready to install');
    expect(html).toContain('>Restart to update</button>');
    expect(html).toContain('>Check now</button>');
  });

  it('hides the channel control for an unmanaged install', () => {
    const html = render({ ...managed, managed: false, state: 'unmanaged', staged: null });

    expect(html).toContain('Unavailable for this install');
    expect(html).not.toContain('aria-label="Release channel"');
    expect(html).not.toContain('<button');
  });

  it('hints how to enable updates while no channel is chosen', () => {
    const html = render({ ...managed, channel: null, state: 'disabled', staged: null });

    expect(html).toContain('aria-pressed="true">Off<');
    expect(html).toContain('Off · choose a channel to enable');
    expect(html).not.toContain('Check now');
    expect(html).not.toContain('Restart to update');
  });

  it('hides Check now while the updater is working', () => {
    const html = render({ ...managed, state: 'downloading', staged: null });

    expect(html).toContain('Downloading v0.3.1…');
    expect(html).not.toContain('Check now');
    expect(html).not.toContain('Restart to update');
  });

  it('keeps Check now after a failed check so the user can retry', () => {
    const html = render({ ...managed, state: 'failed', staged: null, error: 'offline: no route' });

    expect(html).toContain('Failed: offline: no route');
    expect(html).toContain('>Check now</button>');
  });
});
