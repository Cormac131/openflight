import type { HardwareFault, HardwareStatus } from '../types/hardware';

/**
 * The degraded faults worth showing right now.
 *
 * Pulled out of the component so the two rules it encodes are testable
 * without rendering: a blocking fault suppresses the banners (the fault
 * screen is already covering everything, and a strip of secondary warnings
 * behind it would only compete with the one thing that matters), and a fault
 * the owner has waved away this session stays away.
 */
export function visibleDegradedFaults(
  status: HardwareStatus,
  dismissed: string[]
): HardwareFault[] {
  if (status.blocking) return [];
  return status.faults.filter(
    (fault) => fault.severity === 'degraded' && !dismissed.includes(fault.device)
  );
}
