import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClubTagPrompt } from './ClubTagPrompt';
import type { NfcScan } from '../../types/nfc';

const scan: NfcScan = {
  uid: '04A2B1C3',
  uid_display: '04:A2:B1:C3',
  timestamp: 1,
  club: null,
  known: false,
};

function render() {
  return renderToString(<ClubTagPrompt scan={scan} onAssign={() => {}} onDismiss={() => {}} />);
}

describe('ClubTagPrompt', () => {
  it('asks which club the new tag belongs to', () => {
    expect(render()).toContain('aria-label="New club tag"');
  });

  it('shows the tag UID so two blank stickers can be told apart', () => {
    expect(render()).toContain('04:A2:B1:C3');
  });

  it('preselects no club, since a mis-tap here is written to disk', () => {
    expect(render()).not.toContain('picker-overlay__option--selected');
  });

  it('offers every club, not just the group the last selection was in', () => {
    const html = render();

    expect(html).toMatch(/panel-action[^>]*>Irons</);
    expect(html).toMatch(/panel-action[^>]*>Hybrids</);
    expect(html).toMatch(/panel-action[^>]*>Woods</);
  });

  it('can be dismissed without learning the tag', () => {
    expect(render()).toContain('aria-label="Close New club tag"');
  });
});
