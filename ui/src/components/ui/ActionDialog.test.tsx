import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ActionDialog, type ActionDialogLabels, type ActionDialogState } from './ActionDialog';

const noop = () => {};

const labels: ActionDialogLabels = {
  confirm: 'Do the thing?',
  confirmDetail: 'It takes a moment.',
  action: 'Do it',
  cancel: 'Never mind',
  pendingAria: 'Doing the thing',
  pendingTitle: 'Doing the thing…',
  pendingDetail: 'Hold on',
  error: 'Could not do the thing',
  errorDetail: 'Check and retry.',
  tryAgain: 'Retry',
};

function render(state: ActionDialogState) {
  return renderToString(
    <ActionDialog state={state} titleId="thing-title" labels={labels} onConfirm={noop} onCancel={noop} />
  );
}

describe('ActionDialog', () => {
  it('asks for confirmation with the action detail and both buttons', () => {
    const html = render('confirm');

    expect(html).toContain('aria-labelledby="thing-title"');
    expect(html).toContain('Do the thing?');
    expect(html).toContain('It takes a moment.');
    expect(html).toContain('>Do it</button>');
    expect(html).toContain('>Never mind</button>');
    expect(html).not.toContain('Retry');
  });

  it('shows persistent progress with no controls while pending', () => {
    const html = render('pending');

    expect(html).toContain('aria-label="Doing the thing"');
    expect(html).toContain('Doing the thing…');
    expect(html).toContain('Hold on');
    expect(html).not.toContain('<button');
  });

  it('offers retry and cancel after a failure', () => {
    const html = render('error');

    expect(html).toContain('Could not do the thing');
    expect(html).toContain('Check and retry.');
    expect(html).toContain('>Retry</button>');
    expect(html).toContain('>Never mind</button>');
    expect(html).not.toContain('It takes a moment.');
  });
});
