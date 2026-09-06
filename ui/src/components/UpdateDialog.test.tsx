import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { UpdateDialog, type UpdateDialogState } from './UpdateDialog';

const noop = () => {};

function render(state: UpdateDialogState, stagedName: string | null = 'v0.3.1') {
  return renderToString(<UpdateDialog state={state} stagedName={stagedName} onConfirm={noop} onCancel={noop} />);
}

describe('UpdateDialog', () => {
  it('names the staged release and warns that the kiosk goes away', () => {
    const html = render('confirm');

    expect(html).toContain('Restart to install v0.3.1?');
    expect(html).toContain('OpenFlight closes for about a minute');
    expect(html).toContain('>Restart</button>');
    expect(html).toContain('>Cancel</button>');
  });

  it('shows restart progress without controls', () => {
    const html = render('pending');

    expect(html).toContain('aria-label="Restarting OpenFlight"');
    expect(html).toContain('Installing the update and relaunching');
    expect(html).not.toContain('<button');
  });

  it('explains a busy refusal and lets the user retry later', () => {
    const html = render('blocked');

    expect(html).toContain('OpenFlight is busy');
    expect(html).toContain('A shot is still being processed');
    expect(html).toContain('>Try Again</button>');
  });

  it('distinguishes a refused request from a busy one', () => {
    const html = render('error');

    expect(html).toContain('Could not start the update');
    expect(html).not.toContain('OpenFlight is busy');
  });
});
