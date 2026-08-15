export type PowerState = 'plugged_in' | 'on_battery' | 'low' | 'critical' | 'unavailable';

export interface PowerStatus {
  available: boolean;
  state: PowerState;
  battery_percent: number | null;
  battery_voltage_v: number | null;
  external_power: boolean | null;
  updated_at: string;
  error: string | null;
}
