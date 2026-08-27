import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { UpdateAvailableDialog } from './UpdateAvailableDialog';

const noop = () => {};

describe('UpdateAvailableDialog', () => {
  it('shows the release tag and notes in the main prompt', () => {
    const html = renderToString(
      <UpdateAvailableDialog
        state="prompt"
        tag="v0.3.0"
        notes="Bug fixes and improvements"
        onApplyNowClick={noop}
        onApplyConfirmed={noop}
        onCancelConfirm={noop}
        onNextRestart={noop}
        onNever={noop}
      />
    );

    expect(html).toContain('v0.3.0');
    expect(html).toContain('Bug fixes and improvements');
    expect(html).toContain('role="dialog"');
    expect(html).toContain('>Apply now</button>');
    expect(html).toContain('>Next restart</button>');
    expect(html).toContain('>Never</button>');
  });

  it('shows an end-session confirmation with its own Apply now / Back controls', () => {
    const html = renderToString(
      <UpdateAvailableDialog
        state="confirmActiveSession"
        tag="v0.3.0"
        notes=""
        onApplyNowClick={noop}
        onApplyConfirmed={noop}
        onCancelConfirm={noop}
        onNextRestart={noop}
        onNever={noop}
      />
    );

    expect(html).toContain('End this session to update?');
    expect(html).toContain('>Apply now</button>');
    expect(html).toContain('>Back</button>');
    expect(html).not.toContain('>Never</button>');
  });

  it('shows persistent restarting feedback while pending', () => {
    const html = renderToString(
      <UpdateAvailableDialog
        state="pending"
        tag="v0.3.0"
        notes=""
        onApplyNowClick={noop}
        onApplyConfirmed={noop}
        onCancelConfirm={noop}
        onNextRestart={noop}
        onNever={noop}
      />
    );

    expect(html).toContain('Applying update…');
    expect(html).toContain('aria-label="Applying update"');
    expect(html).not.toContain('>Apply now</button>');
    expect(html).not.toContain('>Never</button>');
  });

  it('offers Try Again but not Next restart/Never after a failed apply', () => {
    const html = renderToString(
      <UpdateAvailableDialog
        state="error"
        tag="v0.3.0"
        notes=""
        onApplyNowClick={noop}
        onApplyConfirmed={noop}
        onCancelConfirm={noop}
        onNextRestart={noop}
        onNever={noop}
      />
    );

    expect(html).toContain('Could not start the update');
    expect(html).toContain('>Try Again</button>');
    expect(html).not.toContain('>Next restart</button>');
    expect(html).toContain('>Never</button>');
  });
});
