import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { IWRSetupStatus } from '../types/shot';
import { SetupBanner } from './SetupBanner';

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function status(overrides: Partial<IWRSetupStatus> = {}): IWRSetupStatus {
  return {
    enabled: true,
    state: 'locked',
    follow: false,
    bin: 34,
    range_m: 1.608,
    confidence: 0.93,
    ratio: 8.6,
    reason: 'none',
    label: 'ideal',
    ok: true,
    move_cm: 0,
    message: 'Ready',
    error: null,
    timestamp: 1,
    ...overrides,
  };
}

describe('SetupBanner', () => {
  it('renders nothing without a status or while the detector is off', () => {
    expect(renderToString(<SetupBanner status={null} />)).toBe('');
    expect(renderToString(<SetupBanner status={status({ enabled: false })} />)).toBe('');
    expect(renderToString(<SetupBanner status={status({ state: 'off' })} />)).toBe('');
  });

  it('shows the ball range and Ready in the ideal band', () => {
    const html = text(renderToString(<SetupBanner status={status()} />));
    expect(html).toContain('setup-banner--ok');
    expect(html).toContain('Ball at 1.61 m');
    expect(html).toContain('Ready');
  });

  it('tells the player how far to move OpenFlight, and which way', () => {
    const far = text(
      renderToString(<SetupBanner status={status({ range_m: 2.3, label: 'too-far', ok: false, move_cm: 70 })} />)
    );
    expect(far).toContain('setup-banner--error');
    expect(far).toContain('Move OpenFlight about 70 cm closer to the ball');

    const close = text(
      renderToString(<SetupBanner status={status({ range_m: 1.4, label: 'close', ok: true, move_cm: -20 })} />)
    );
    expect(close).toContain('setup-banner--warn');
    expect(close).toContain('Usable. Move OpenFlight about 20 cm back from the ball');
  });

  it('asks for a ball while waiting and explains the other detector states', () => {
    const waiting = text(
      renderToString(
        <SetupBanner
          status={status({ state: 'waiting', bin: null, range_m: null, label: null, ok: null, move_cm: null })}
        />
      )
    );
    expect(waiting).toContain('setup-banner--warn');
    expect(waiting).toContain('Place a ball on the tee');
    expect(text(renderToString(<SetupBanner status={status({ state: 'building', range_m: null })} />))).toContain(
      'learning the background'
    );
    expect(text(renderToString(<SetupBanner status={status({ state: 'candidate', range_m: null })} />))).toContain(
      'Ball seen, confirming'
    );
  });

  it('reports a failed poll with its error', () => {
    const html = text(
      renderToString(<SetupBanner status={status({ state: 'error', range_m: null, error: 'serial timeout' })} />)
    );
    expect(html).toContain('setup-banner--error');
    expect(html).toContain('TI radar setup status unavailable');
    expect(html).toContain('serial timeout');
  });
});
