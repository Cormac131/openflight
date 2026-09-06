import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale, t } from '../i18n';
import type { UpdateStatus } from '../types/update';
import { updateChannelValue, updateInProgress, updateStatusLabel } from './updateLabel';

const base: UpdateStatus = {
  format_version: 1,
  managed: true,
  state: 'up_to_date',
  channel: 'stable',
  repository: 'open-flight/openflight',
  current: { tag: 'v0.3.0', version: '0.3.0', channel: 'stable' },
  available: null,
  staged: null,
  previous: null,
  pending_confirm: false,
  last_check_at: '2026-09-06T08:00:00+00:00',
  error: null,
};

const staged = { name: 'v0.3.1', tag: 'v0.3.1', version: '0.3.1', channel: 'stable' };
const available = { tag: 'v0.3.1', version: '0.3.1', size: 6_500_000 };

describe('updateStatusLabel', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it.each<[UpdateStatus['state'], Partial<UpdateStatus>, string]>([
    ['unmanaged', { managed: false }, 'Unavailable for this install'],
    ['disabled', { channel: null }, 'Off · choose a channel to enable'],
    ['checking', {}, 'Checking…'],
    ['downloading', { available }, 'Downloading v0.3.1…'],
    ['staging', { available }, 'Preparing v0.3.1…'],
    ['staged', { staged }, 'v0.3.1 ready to install'],
    ['pending_confirm', {}, 'Confirming 0.3.0'],
    ['up_to_date', {}, 'Up to date'],
    ['held_back', { available }, 'v0.3.1 skipped after a failed start'],
    ['failed', { error: 'offline: no route' }, 'Failed: offline: no route'],
    ['restarting', { staged }, 'Restarting…'],
  ])('labels %s', (state, extra, expected) => {
    expect(updateStatusLabel({ ...base, ...extra, state }, t)).toBe(expected);
  });

  it('reports Unavailable until the server has sent update_status', () => {
    expect(updateStatusLabel(null, t)).toBe('Unavailable');
  });

  it('follows the active locale', () => {
    setActiveLocale('fr');

    expect(updateStatusLabel(base, t)).toBe('À jour');
  });
});

describe('updateChannelValue', () => {
  it('maps no channel to the off segment', () => {
    expect(updateChannelValue(null)).toBe('off');
    expect(updateChannelValue({ ...base, channel: null })).toBe('off');
    expect(updateChannelValue({ ...base, channel: 'experimental' })).toBe('experimental');
  });
});

describe('updateInProgress', () => {
  it('is true only while the updater is working', () => {
    expect(updateInProgress(null)).toBe(false);
    for (const state of ['checking', 'downloading', 'staging', 'restarting'] as const) {
      expect(updateInProgress({ ...base, state })).toBe(true);
    }
    for (const state of ['up_to_date', 'staged', 'failed', 'held_back', 'disabled', 'unmanaged'] as const) {
      expect(updateInProgress({ ...base, state })).toBe(false);
    }
  });
});
