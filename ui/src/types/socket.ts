export interface DebugReading {
  speed: number;
  direction: 'inbound' | 'outbound' | 'unknown';
  magnitude: number | null;
  timestamp: string;
}

export type SimState = 'connected' | 'connecting' | 'reconnecting' | 'disabled' | 'stopped' | 'error';

export interface SimStatus {
  target: string;
  state: SimState;
  host?: string;
  port?: number;
  message?: string;
  attempt?: number;
  next_retry_in_s?: number;
}

export interface SimShotInfo {
  target: string;
  shot_number: number;
  fields: string[];
  values: Record<string, number | null>;
  provenance: Record<string, 'measured' | 'estimated'>;
}

export interface SwingSpeedEvent {
  peak_speed_mph: number;
  timestamp: string;
  duration_ms: number;
  reading_count: number;
  trigger_speed_mph: number;
  peak_magnitude: number | null;
  player_name?: string;
  unit: string;
  mode: 'swing-speed';
}

export interface RadarConfig {
  min_speed: number;
  max_speed: number;
  min_magnitude: number;
  transmit_power: number;
}

/**
 * State of the DS3502 digital potentiometer fitted to the SEN-14262 R17 pad.
 *
 * `position` is the wiper step (0 = least sensitive), read back from the chip
 * itself; it is null only when the device cannot be reached. Every derived
 * figure (`sensitivity_percent`, the resistances) is computed server-side from
 * that step so the maths lives in exactly one place.
 */
export interface SoundSensitivity {
  enabled: boolean;
  position: number | null;
  max_position: number;
  default_position: number;
  sensitivity_percent: number | null;
  /** What R17 presents: the series resistor plus the DS3502 wiper. */
  resistance_ohms: number | null;
  /** R17 in parallel with the board's 100k R3, i.e. the actual preamp gain leg. */
  preamp_feedback_ohms: number | null;
  /** The fixed resistor in series with the wiper, which sets where the span sits. */
  series_ohms: number;
  simulated: boolean;
  error: string | null;
}

export interface DebugShotLog {
  type: 'shot';
  timestamp: string;
  radar: {
    ball_speed_mph: number;
    club_speed_mph: number | null;
    smash_factor: number | null;
    peak_magnitude: number;
  };
  camera: {
    launch_angle_vertical: number;
    launch_angle_horizontal: number;
    launch_angle_confidence: number;
    positions_tracked: number;
    launch_detected: boolean;
  } | null;
  club: string;
}
