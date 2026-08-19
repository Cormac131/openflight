import { useMemo } from 'react';
import type { Shot } from '../types/shot';
import { computeSwingSpeedStats, getSwingSpeedMph, isSwingSpeedShot } from '../types/shot';
import { useUnitPreference } from '../state/useUnitPreference';
import type { UnitSystem } from '../utils/units';
import { formatCarryRange, formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../utils/units';
import { MetricCard } from './ui/MetricCard';
import './ShotDisplay.css';

interface ShotDisplayProps {
  shot: Shot | null;
  shots?: Shot[];
  animate?: boolean;
  activePlayerName?: string;
  activeTrainingImplement?: string;
}

const MISSING = '—';

function formatSpinRpm(rpm: number): string {
  return rpm.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function getLaunchAngleQuality(confidence: number | null): 'high' | 'medium' | 'low' | null {
  if (confidence === null) return null;
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

function formatSignedDegrees(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)}`;
}

function ShotMetricGrid({ shot, unitSystem }: { shot: Shot | null; unitSystem: UnitSystem }) {
  const hasLaunchAngle = shot?.launch_angle_vertical !== null && shot?.launch_angle_vertical !== undefined;
  const hasHorizontalLaunch = shot?.launch_angle_horizontal !== null && shot?.launch_angle_horizontal !== undefined;
  const hasClubSpeed = Boolean(shot?.club_speed_mph);
  const hasAoA = shot?.club_angle_deg !== null && shot?.club_angle_deg !== undefined;
  const hasPath = shot?.club_path_deg !== null && shot?.club_path_deg !== undefined;
  const hasSpinAxis = shot?.spin_axis_deg !== null && shot?.spin_axis_deg !== undefined;
  const hasSpin = shot?.spin_rpm !== null && shot?.spin_rpm !== undefined;

  const displayCarry = shot ? (shot.carry_spin_adjusted ?? shot.estimated_carry_yards ?? 0) : null;
  const carryRange = shot ? formatCarryRange(shot.carry_range, unitSystem) : null;
  const carrySubtext = shot?.carry_spin_adjusted ? 'spin-adjusted' : carryRange || undefined;

  return (
    <div className="shot-display__metrics">
      <MetricCard
        variant="emphasis"
        value={shot ? formatDistance(displayCarry ?? 0, unitSystem, 0) : MISSING}
        unit={shot ? getDistanceUnit(unitSystem) : undefined}
        label="Est. Carry"
        subtext={shot ? carrySubtext : undefined}
      />
      <MetricCard
        value={hasLaunchAngle ? shot.launch_angle_vertical!.toFixed(1) : MISSING}
        unit={hasLaunchAngle ? '°' : undefined}
        label="V. Launch"
        subtext={hasLaunchAngle ? (shot.angle_source ?? undefined) : undefined}
        confidence={hasLaunchAngle ? getLaunchAngleQuality(shot.launch_angle_confidence) : null}
      />
      <MetricCard
        value={hasPath ? formatSignedDegrees(shot.club_path_deg!) : MISSING}
        unit={hasPath ? '°' : undefined}
        label="Club Path"
        subtext={hasPath ? 'radar' : undefined}
      />
      <MetricCard
        value={hasHorizontalLaunch ? formatSignedDegrees(shot.launch_angle_horizontal!) : MISSING}
        unit={hasHorizontalLaunch ? '°' : undefined}
        label="H. Launch"
        subtext={hasHorizontalLaunch ? (shot.angle_source ?? undefined) : undefined}
        confidence={hasHorizontalLaunch ? getLaunchAngleQuality(shot.launch_angle_confidence) : null}
      />
      <MetricCard
        value={hasClubSpeed ? formatSpeed(shot.club_speed_mph!, unitSystem, 1) : MISSING}
        unit={hasClubSpeed ? getSpeedUnit(unitSystem) : undefined}
        label="Club Speed"
        subtext={shot?.smash_factor ? `${shot.smash_factor.toFixed(2)} smash` : undefined}
      />
      <MetricCard
        value={hasAoA ? shot.club_angle_deg!.toFixed(1) : MISSING}
        unit={hasAoA ? '°' : undefined}
        label="Club AoA"
        subtext={hasAoA ? 'radar' : undefined}
      />
      <MetricCard
        value={hasSpinAxis ? formatSignedDegrees(shot.spin_axis_deg!) : MISSING}
        unit={hasSpinAxis ? '°' : undefined}
        label="Spin Axis"
        subtext={
          hasSpinAxis ? (shot.spin_axis_deg! > 2 ? 'fade' : shot.spin_axis_deg! < -2 ? 'draw' : 'straight') : undefined
        }
      />
      <MetricCard
        value={hasSpin ? formatSpinRpm(shot.spin_rpm!) : MISSING}
        unit={hasSpin ? 'rpm' : undefined}
        label="Spin Rate"
        subtext={hasSpin && shot.spin_source ? (shot.spin_source === 'calculated' ? 'estimated' : 'radar') : undefined}
        confidence={hasSpin ? shot.spin_quality : null}
      />
    </div>
  );
}

export function ShotDisplay({
  shot,
  shots = [],
  animate = false,
  activePlayerName,
  activeTrainingImplement,
}: ShotDisplayProps) {
  const { unitSystem } = useUnitPreference();
  const swingStats = useMemo(
    () =>
      computeSwingSpeedStats(shots, {
        playerName: activePlayerName,
        trainingImplement: activeTrainingImplement,
      }),
    [shots, activePlayerName, activeTrainingImplement]
  );

  if (!shot) {
    return (
      <div className="shot-display shot-display--empty">
        <div className="shot-display__layout">
          <div className="shot-display__primary">
            <MetricCard size="hero" variant="emphasis" value={MISSING} label="Ball Speed" />
            <p className="shot-display__waiting-text">Ready</p>
            <p className="shot-display__waiting-hint">Start a shot or swing speed session</p>
          </div>
          <ShotMetricGrid shot={null} unitSystem={unitSystem} />
        </div>
      </div>
    );
  }

  if (isSwingSpeedShot(shot)) {
    const lastSpeed = getSwingSpeedMph(shot);
    const readingDetail =
      shot.swing_speed_reading_count !== undefined ? `${shot.swing_speed_reading_count} radar readings` : undefined;

    return (
      <div className={`shot-display shot-display--swing-speed ${animate ? 'shot-display--animate' : ''}`}>
        <div className="shot-display__layout">
          <div className="shot-display__primary">
            <MetricCard
              size="hero"
              variant="emphasis"
              value={formatSpeed(lastSpeed, unitSystem, 1)}
              unit={getSpeedUnit(unitSystem)}
              label="Last Swing"
            />
          </div>
          <div className="shot-display__metrics shot-display__metrics--swing-speed">
            <MetricCard
              variant="emphasis"
              value={formatSpeed(swingStats.best_speed_mph, unitSystem, 1)}
              unit={getSpeedUnit(unitSystem)}
              label="Best"
              subtext="player + implement"
            />
            <MetricCard
              value={formatSpeed(swingStats.avg_speed_mph, unitSystem, 1)}
              unit={getSpeedUnit(unitSystem)}
              label="Average"
              subtext={`${swingStats.count} swings`}
            />
            <MetricCard value={swingStats.count} label="Swing Count" subtext={readingDetail} />
            <MetricCard
              value={shot.training_implement_label ?? shot.club}
              label="Implement"
              subtext={
                shot.swing_speed_trigger_mph !== undefined
                  ? `${formatSpeed(shot.swing_speed_trigger_mph, unitSystem, 1)} ${getSpeedUnit(unitSystem)} trigger`
                  : 'selected'
              }
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`shot-display ${animate ? 'shot-display--animate' : ''}`}>
      <div className="shot-display__layout">
        <div className="shot-display__primary">
          <MetricCard
            size="hero"
            variant="emphasis"
            value={formatSpeed(shot.ball_speed_mph, unitSystem, 1)}
            unit={getSpeedUnit(unitSystem)}
            label="Ball Speed"
          />
        </div>
        <ShotMetricGrid shot={shot} unitSystem={unitSystem} />
      </div>
    </div>
  );
}
