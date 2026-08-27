/**
 * Hardware that was asked for and failed to start.
 *
 * Mirrors `openflight/hardware_status.py`. The server sends this on connect
 * and whenever the picture changes, so the kiosk can say "the radar is not
 * plugged in" instead of simply never appearing.
 */

/** Device ids, matching `provisioning.detect.DeviceKind` on the server. */
export type HardwareDevice =
  | 'ops243'
  | 'iwr6843'
  | 'kld7_vertical'
  | 'kld7_horizontal'
  | 'inclinometer'
  | 'battery'
  | 'camera';

/**
 * `blocking` means no shot can be measured at all — only the OPS243-A, which
 * is the ball-speed source. `degraded` means the session still works with
 * less data. The distinction decides whether the UI covers the screen or
 * shows a dismissible banner.
 */
export type HardwareSeverity = 'blocking' | 'degraded';

export interface HardwareFault {
  device: HardwareDevice;
  severity: HardwareSeverity;
  /** Short owner-facing headline, e.g. "Radar not found". */
  title: string;
  /** What to try, in plain language. */
  remedy: string;
  /** The underlying error. Shown small — it is for a support request. */
  detail: string;
}

export interface HardwareStatus {
  /** Whether the OPS243 serial link is open right now. */
  radar_connected: boolean;
  /** True only when the radar is live and nothing failed to start. */
  ok: boolean;
  /** The fault that makes the product unusable, if there is one. */
  blocking: HardwareFault | null;
  /** Every fault, blocking first. */
  faults: HardwareFault[];
}

/**
 * What the UI assumes before the server has said anything.
 *
 * Optimistic on purpose: the socket takes a moment to deliver the first
 * status, and flashing "Radar not found" during a normal start-up would
 * train owners to ignore the screen that matters.
 */
export const UNKNOWN_HARDWARE_STATUS: HardwareStatus = {
  radar_connected: true,
  ok: true,
  blocking: null,
  faults: [],
};
