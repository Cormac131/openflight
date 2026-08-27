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

/** Where the air conditions used for carry came from, best source first. */
export type AirConditionsSource = 'sensor' | 'sensor_stale' | 'config' | 'standard';

export interface AirReading {
  applied: boolean;
  status: string;
  age_s: number | null;
  pressure_hpa?: number;
  pressure_pa?: number;
  pressure_std_pa?: number;
  /** Offset-corrected temperature, the one density is computed from. */
  temperature_c?: number;
  /** Uncorrected die reading. A large gap to temperature_c is self-heating. */
  raw_temperature_c?: number;
  density_kg_m3?: number;
  sample_count?: number;
}

export interface AirSensorConfig {
  enabled: boolean;
  sensor?: string;
  i2c_bus?: number;
  i2c_address?: string;
  sample_hz?: number;
  temperature_offset_c?: number;
  error?: string;
}

export interface AirStatus {
  source: AirConditionsSource;
  density_kg_m3: number;
  pressure_hpa: number;
  temperature_c: number;
  elevation_ft: number | null;
  normalization_density_kg_m3: number;
  density_delta_pct: number;
  /** Density difference expressed as driver carry, the readable unit. */
  driver_carry_delta_yards: number;
  sensor: AirSensorConfig;
  reading?: AirReading;
}
