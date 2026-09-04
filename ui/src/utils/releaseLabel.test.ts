import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale, t } from '../i18n';
import type { ReleaseInfo } from '../types/release';
import { releaseVersionLabel } from './releaseLabel';

const release: ReleaseInfo = {
  format_version: 1,
  version: '0.3.0-dev.7',
  base_version: '0.3.0',
  channel: 'experimental',
  tag: 'v0.3.0-dev.7',
  commit: '0123456789ab',
  built_at: '2026-09-04T12:00:00+00:00',
  repository: 'open-flight/openflight',
};

describe('releaseVersionLabel', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it('joins the build version with its translated channel', () => {
    expect(releaseVersionLabel(release, t)).toBe('0.3.0-dev.7 · Experimental');
    expect(releaseVersionLabel({ ...release, version: '0.3.0', channel: 'stable' }, t)).toBe('0.3.0 · Stable');
    expect(releaseVersionLabel({ ...release, version: '0.3.0+0123456789ab', channel: 'source', tag: null }, t)).toBe(
      '0.3.0+0123456789ab · Source checkout'
    );
  });

  it('follows the active locale', () => {
    setActiveLocale('es');

    expect(releaseVersionLabel({ ...release, version: '0.3.0', channel: 'stable' }, t)).toBe('0.3.0 · Estable');
  });

  it('reports Unavailable until the server has sent release_info', () => {
    expect(releaseVersionLabel(null, t)).toBe('Unavailable');
  });
});
