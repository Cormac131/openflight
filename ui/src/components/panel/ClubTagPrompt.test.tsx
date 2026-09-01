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
  source: null,
  blank: true,
  writable: false,
};

function render(overrides: Partial<NfcScan> = {}, error?: string) {
  return renderToString(
    <ClubTagPrompt
      scan={{ ...scan, ...overrides }}
      error={error}
      onAssign={() => {}}
      onDismiss={() => {}}
      onForget={() => {}}
    />
  );
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

  it('does not offer Forget on an unlearned tag', () => {
    expect(render()).not.toContain('Forget');
  });

  it('offers Forget only for the scanned known tag', () => {
    const html = render({ known: true, club: '7-iron', blank: false });

    expect(html).toContain('aria-label="Club tag"');
    expect(html).toContain('aria-label="Forget the tag for 7 Iron"');
    expect(html).toContain('picker-overlay__option--selected');
  });

  it('shows a failed assignment so the operator can retry', () => {
    const html = render({}, 'Could not save club tags: read-only filesystem');

    expect(html).toContain('Could not save this tag');
    expect(html).toContain('Could not save club tags: read-only filesystem');
    expect(html).toContain('role="alert"');
  });
});
