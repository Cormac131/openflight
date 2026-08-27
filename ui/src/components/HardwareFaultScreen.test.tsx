import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { HardwareFault, HardwareStatus } from '../types/hardware';
import { UNKNOWN_HARDWARE_STATUS } from '../types/hardware';
import { visibleDegradedFaults } from '../services/hardwareFaults';
import { HardwareFaultBanner } from './HardwareFaultBanner';
import { HardwareFaultScreen } from './HardwareFaultScreen';

const fault = (overrides: Partial<HardwareFault> = {}): HardwareFault => ({
  device: 'ops243',
  severity: 'blocking',
  title: 'Radar not found',
  remedy: "Check that the radar's USB cable is connected at both ends.",
  detail: 'No OPS243 radar found on USB.',
  ...overrides,
});

const status = (overrides: Partial<HardwareStatus> = {}): HardwareStatus => ({
  radar_connected: false,
  ok: false,
  blocking: null,
  faults: [],
  ...overrides,
});

describe('HardwareFaultScreen', () => {
  it('shows the headline and the remedy', () => {
    const html = renderToString(<HardwareFaultScreen fault={fault()} serverConnected />);

    expect(html).toContain('Radar not found');
    expect(html).toContain('USB cable is connected at both ends');
  });

  it('shows the underlying error for a support request', () => {
    const html = renderToString(<HardwareFaultScreen fault={fault()} serverConnected />);

    expect(html).toContain('No OPS243 radar found on USB.');
  });

  it('omits the detail block when there is no detail', () => {
    const html = renderToString(
      <HardwareFaultScreen fault={fault({ detail: '' })} serverConnected />
    );

    expect(html).not.toContain('hardware-fault__detail');
  });

  it('adds an offline note when the server is unreachable', () => {
    const html = renderToString(<HardwareFaultScreen fault={fault()} serverConnected={false} />);

    expect(html).toContain('not responding');
  });

  it('omits the offline note when the server is up', () => {
    const html = renderToString(<HardwareFaultScreen fault={fault()} serverConnected />);

    expect(html).not.toContain('hardware-fault__offline');
  });

  it('announces itself assertively so it is not missed', () => {
    const html = renderToString(<HardwareFaultScreen fault={fault()} serverConnected />);

    expect(html).toContain('role="alert"');
    expect(html).toContain('aria-live="assertive"');
  });

  it('offers no way to dismiss it', () => {
    // There is no working product behind this screen, so a dismiss control
    // would only hide the explanation and leave an empty app.
    const html = renderToString(<HardwareFaultScreen fault={fault()} serverConnected />);

    expect(html).not.toContain('<button');
  });
});

describe('HardwareFaultBanner', () => {
  const degraded = fault({
    device: 'iwr6843',
    severity: 'degraded',
    title: '60 GHz radar unavailable',
    remedy: "Check the 60 GHz radar's USB cable.",
  });

  it('renders nothing when there are no faults', () => {
    expect(renderToString(<HardwareFaultBanner faults={[]} onDismiss={() => {}} />)).toBe('');
  });

  it('shows the title and remedy', () => {
    const html = renderToString(<HardwareFaultBanner faults={[degraded]} onDismiss={() => {}} />);

    expect(html).toContain('60 GHz radar unavailable');
    // renderToString escapes the apostrophe, so match either side of it.
    expect(html).toContain('USB cable');
  });

  it('is dismissible, unlike the blocking screen', () => {
    const html = renderToString(<HardwareFaultBanner faults={[degraded]} onDismiss={() => {}} />);

    expect(html).toContain('<button');
  });

  it('renders one strip per fault', () => {
    const second = fault({ device: 'battery', severity: 'degraded', title: 'Battery monitoring unavailable' });
    const html = renderToString(
      <HardwareFaultBanner faults={[degraded, second]} onDismiss={() => {}} />
    );

    expect(html.match(/hardware-banner__text/g)).toHaveLength(2);
  });
});

describe('visibleDegradedFaults', () => {
  const degraded = fault({ device: 'iwr6843', severity: 'degraded' });

  it('shows degraded faults', () => {
    expect(visibleDegradedFaults(status({ faults: [degraded] }), [])).toHaveLength(1);
  });

  it('hides everything behind a blocking fault', () => {
    // The fault screen already covers the app; competing warnings behind it
    // would only distract from the one thing that matters.
    const blocking = fault();
    const result = visibleDegradedFaults(
      status({ blocking, faults: [blocking, degraded] }),
      []
    );

    expect(result).toEqual([]);
  });

  it('never surfaces a blocking fault as a banner', () => {
    const blocking = fault();
    expect(visibleDegradedFaults(status({ faults: [blocking] }), [])).toEqual([]);
  });

  it('respects a dismissal', () => {
    expect(visibleDegradedFaults(status({ faults: [degraded] }), ['iwr6843'])).toEqual([]);
  });

  it('keeps other faults visible when one is dismissed', () => {
    const other = fault({ device: 'battery', severity: 'degraded' });
    const result = visibleDegradedFaults(status({ faults: [degraded, other] }), ['iwr6843']);

    expect(result).toHaveLength(1);
    expect(result[0].device).toBe('battery');
  });

  it('shows nothing for a healthy system', () => {
    expect(visibleDegradedFaults(UNKNOWN_HARDWARE_STATUS, [])).toEqual([]);
  });
});

describe('UNKNOWN_HARDWARE_STATUS', () => {
  it('is optimistic so start-up does not flash a fault screen', () => {
    // The socket takes a moment to deliver the first status; assuming the
    // worst would train owners to ignore the screen that matters.
    expect(UNKNOWN_HARDWARE_STATUS.blocking).toBeNull();
    expect(UNKNOWN_HARDWARE_STATUS.radar_connected).toBe(true);
  });
});
