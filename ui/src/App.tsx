import { useState, useEffect } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useSocket } from './hooks/useSocket';
import { useSystemStore } from './stores/useSystemStore';
import { useShotStore } from './stores/useShotStore';
import { useCameraStore } from './stores/useCameraStore';
import { useDebugStore } from './stores/useDebugStore';
import { usePlayerStore } from './stores/usePlayerStore';
import { socketService } from './services/socketService';
import { ShotDisplay } from './components/ShotDisplay';
import { StatsView } from './components/StatsView';
import { ShotList } from './components/ShotList';
import { DebugPanel } from './components/DebugPanel';
import { CameraFeed } from './components/CameraFeed';
import { ConnectionStatus } from './components/ConnectionStatus';
import { PowerExperience } from './components/PowerStatus';
import { SimStatus } from './components/SimStatus';
import { SimShotBadges } from './components/SimShotBadges';
import { ClubPicker } from './components/ClubPicker';
import { ClubSelectScreen } from './components/ClubSelectScreen';
import { TrainingImplementPicker } from './components/TrainingImplementPicker';
import { PlayerPicker } from './components/PlayerPicker';
import { BallDetectionIndicator } from './components/BallDetectionIndicator';
import { DisplayMode } from './components/DisplayMode';
import { ShotProcessingArea } from './components/ShotProcessingArea';
import { ShutdownDialog, type ShutdownState } from './components/ShutdownDialog';
import { unlockAudioCue } from './utils/audioCue';
import {
  useLaunchDaddy,
  LaunchDaddyOverlay,
  LaunchDaddyBrand,
  LaunchDaddySecretIndicator,
} from './components/LaunchDaddy';
import { useUnitPreference } from './state/useUnitPreference';
import { useThemeStore } from './stores/useThemeStore';
import { Button } from './components/ui/Button';
import { SegmentedControl } from './components/ui/SegmentedControl';
import { TabBar } from './components/ui/TabBar';

import Logo from './logo/Logo';

import './App.css';

type View = 'live' | 'stats' | 'shots' | 'camera' | 'debug';

// Navigation icons as inline SVGs for better control
const Icons = {
  live: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
      <path d="M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  ),
  stats: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <path d="M18 20V10M12 20V4M6 20v-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  shots: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  camera: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  ),
  debug: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
};

function AppContent() {
  const { shutdown } = useSocket();
  const { connected, mockMode, debugMode, simStatuses, latestSimShots, serverClub } = useSystemStore(
    useShallow((state) => ({
      connected: state.connected,
      mockMode: state.mockMode,
      debugMode: state.debugMode,
      simStatuses: state.simStatuses,
      latestSimShots: state.latestSimShots,
      serverClub: state.serverClub,
    }))
  );
  const { latestShot, shots, isNewShot, shotProcessingPhase, shotVersion } = useShotStore(
    useShallow((state) => ({
      latestShot: state.latestShot,
      shots: state.shots,
      isNewShot: state.isNewShot,
      shotProcessingPhase: state.shotProcessingPhase,
      shotVersion: state.shotVersion,
    }))
  );
  const cameraStatus = useCameraStore((state) => state.cameraStatus);
  const selectedPlayer = usePlayerStore((state) => state.selectedPlayer);
  const {
    debugReadings,
    debugShotLogs,
    radarConfig,
    triggerDiagnostics,
    triggerStatus,
    iwr6843Alert,
    dismissIWR6843Alert,
  } = useDebugStore(
    useShallow((state) => ({
      debugReadings: state.debugReadings,
      debugShotLogs: state.debugShotLogs,
      radarConfig: state.radarConfig,
      triggerDiagnostics: state.triggerDiagnostics,
      triggerStatus: state.triggerStatus,
      iwr6843Alert: state.iwr6843Alert,
      dismissIWR6843Alert: state.dismissIWR6843Alert,
    }))
  );

  const [currentView, setCurrentView] = useState<View>('live');
  const [selectedClub, setSelectedClub] = useState('driver');
  const [selectedTrainingImplement, setSelectedTrainingImplement] = useState('driver');
  // Reflect a server-pushed club change (e.g. the club changed in the connected
  // simulator) in the local picker, without echoing back to the server. Done
  // during render (React's "adjust state when an input changes" pattern) rather
  // than in an effect, which avoids a cascading-render lint error.
  const [appliedServerClub, setAppliedServerClub] = useState<string | null>(null);
  if (serverClub && serverClub !== appliedServerClub) {
    setAppliedServerClub(serverClub);
    setSelectedClub(serverClub);
  }
  // Shown on every app load so the user confirms their club before the first
  // shot (skippable, keeps the default). The /display route returns early
  // below, so this interstitial never appears in the passive TV view.
  const [showClubSelect, setShowClubSelect] = useState(true);
  const [showShutdown, setShowShutdown] = useState(false);
  const [shutdownState, setShutdownState] = useState<ShutdownState>('confirm');
  const { isLaunchDaddyMode, isExploding, triggerExplosion, handleSecretTap } = useLaunchDaddy();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const logoVariant = theme === 'dark' ? 'light' : 'dark';
  const isDisplayRoute = typeof window !== 'undefined' && window.location.pathname.replace(/\/$/, '') === '/display';
  const isSwingSpeedMode = triggerStatus.mode === 'swing-speed';

  // Trigger explosion when a new shot is detected in Launch Daddy mode
  useEffect(() => {
    if (isNewShot && isLaunchDaddyMode) {
      triggerExplosion();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shotVersion triggers the effect; isNewShot is only a guard
  }, [shotVersion, isLaunchDaddyMode, triggerExplosion]);

  useEffect(() => {
    const unlock = () => unlockAudioCue();
    window.addEventListener('pointerdown', unlock, { once: true });
    window.addEventListener('keydown', unlock, { once: true });

    return () => {
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, []);

  const handleClubChange = (club: string) => {
    setSelectedClub(club);
    socketService.setClub(club);
  };

  const handleTrainingImplementChange = (implement: string) => {
    setSelectedTrainingImplement(implement);
    socketService.setTrainingImplement(implement);
  };

  const handleShutdown = async () => {
    setShutdownState('pending');
    try {
      await shutdown();
    } catch {
      setShutdownState('error');
    }
  };

  const closeShutdown = () => {
    setShowShutdown(false);
    setShutdownState('confirm');
  };

  if (isDisplayRoute) {
    return <DisplayMode connected={connected} cameraStatus={cameraStatus} latestShot={latestShot} shots={shots} />;
  }

  return (
    <div className={`app ${isLaunchDaddyMode ? 'app--launch-daddy' : ''} ${isExploding ? 'app--exploding' : ''}`}>
      {showClubSelect && (
        <ClubSelectScreen
          selectedClub={selectedClub}
          onSelect={(club) => {
            handleClubChange(club);
            setShowClubSelect(false);
          }}
          onSkip={() => setShowClubSelect(false)}
        />
      )}

      {/* Launch Daddy Overlay */}
      <LaunchDaddyOverlay />
      <LaunchDaddySecretIndicator />

      <header className="header">
        {/* Secret activation area - click/tap 5 times quickly */}
        <div
          className="header__secret-tap"
          onClick={handleSecretTap}
          onKeyDown={(e) => e.key === 'Enter' && handleSecretTap()}
          role="button"
          tabIndex={0}
          style={{
            padding: '8px',
            cursor: 'pointer',
            minWidth: '44px',
            minHeight: '44px',
            display: 'flex',
            alignItems: 'center',
            userSelect: 'none',
          }}
        >
          {isLaunchDaddyMode ? <LaunchDaddyBrand /> : <Logo size="small" variant={logoVariant} />}
        </div>
        <div className="header__controls">
          <SegmentedControl
            ariaLabel="Theme"
            value={theme}
            options={[
              { id: 'dark', label: 'DARK' },
              { id: 'light', label: 'LIGHT' },
            ]}
            onChange={setTheme}
          />
          <SegmentedControl
            ariaLabel="Display units"
            value={unitSystem}
            options={[
              { id: 'imperial', label: 'MPH/YDS' },
              { id: 'metric', label: 'KMH/M' },
            ]}
            onChange={setUnitSystem}
          />
          <PlayerPicker />
          {isSwingSpeedMode ? (
            <TrainingImplementPicker
              selectedImplement={selectedTrainingImplement}
              onImplementChange={handleTrainingImplementChange}
            />
          ) : (
            <ClubPicker selectedClub={selectedClub} onClubChange={handleClubChange} />
          )}
          <BallDetectionIndicator
            available={cameraStatus.available}
            enabled={cameraStatus.enabled}
            detected={cameraStatus.ball_detected}
            confidence={cameraStatus.ball_confidence}
            onToggle={() => socketService.toggleCamera()}
          />
          <SimStatus statuses={simStatuses} />
          <PowerExperience />
          <ConnectionStatus connected={connected} />
          <button
            className="power-button"
            onClick={() => {
              setShutdownState('confirm');
              setShowShutdown(true);
            }}
            title="Shut down"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              width="20"
              height="20"
            >
              <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
              <line x1="12" y1="2" x2="12" y2="12" />
            </svg>
          </button>
        </div>
      </header>

      {iwr6843Alert && (
        <div className="iwr-alert" role="alert">
          <div>
            <strong>TI radar capture failed</strong>
            <span>This shot used an estimated launch angle. {iwr6843Alert.reason}</span>
          </div>
          <button type="button" onClick={dismissIWR6843Alert} aria-label="Dismiss TI radar alert">
            Dismiss
          </button>
        </div>
      )}

      {showShutdown ? (
        <ShutdownDialog state={shutdownState} onConfirm={handleShutdown} onCancel={closeShutdown} />
      ) : null}

      <TabBar
        value={currentView}
        onChange={setCurrentView}
        options={[
          { id: 'live', label: 'Live', icon: Icons.live },
          { id: 'stats', label: 'Stats', icon: Icons.stats },
          {
            id: 'shots',
            label: 'Shots',
            icon: Icons.shots,
            badge: shots.length > 0 ? <span className="nav__badge">{shots.length}</span> : undefined,
          },
          {
            id: 'camera',
            label: 'Camera',
            icon: Icons.camera,
            extraClassName: cameraStatus.streaming ? 'nav__button--streaming' : undefined,
            badge: cameraStatus.ball_detected ? <span className="nav__ball-dot" /> : undefined,
          },
          {
            id: 'debug',
            label: 'Debug',
            icon: Icons.debug,
            extraClassName: debugMode ? 'nav__button--recording' : undefined,
            badge: debugMode ? <span className="nav__recording-dot" /> : undefined,
          },
        ]}
      />

      <main className="main">
        {currentView === 'live' && (
          <div className="live-view">
            {isNewShot && <div key={shotVersion} className="shot-flash" />}
            <ShotProcessingArea phase={shotProcessingPhase}>
              <ShotDisplay
                key={shotVersion}
                shot={latestShot}
                shots={shots}
                animate={isNewShot}
                activePlayerName={selectedPlayer}
                activeTrainingImplement={isSwingSpeedMode ? selectedTrainingImplement : undefined}
              />
            </ShotProcessingArea>
            {debugMode && <SimShotBadges latestSimShots={latestSimShots} />}
            {mockMode ? (
              <Button variant="primary" className="ui-button--block" onClick={() => socketService.simulateShot()}>
                {isSwingSpeedMode ? 'Simulate Swing' : 'Simulate Shot'}
              </Button>
            ) : null}
          </div>
        )}
        {currentView === 'stats' && (
          <StatsView shots={shots} activeClub={selectedClub} onClearSession={() => socketService.clearSession()} />
        )}
        {currentView === 'shots' && (
          <ShotList shots={shots} onDeleteShot={(timestamp) => socketService.deleteShot(timestamp)} />
        )}
        {currentView === 'camera' && (
          <CameraFeed
            cameraStatus={cameraStatus}
            onToggleCamera={() => socketService.toggleCamera()}
            onToggleStream={() => socketService.toggleCameraStream()}
          />
        )}
        {currentView === 'debug' && (
          <DebugPanel
            enabled={debugMode}
            readings={debugReadings}
            shotLogs={debugShotLogs}
            radarConfig={radarConfig}
            cameraStatus={cameraStatus}
            mockMode={mockMode}
            onToggle={() => socketService.toggleDebug()}
            onUpdateConfig={(config) => socketService.setRadarConfig(config)}
            triggerDiagnostics={triggerDiagnostics}
            triggerStatus={triggerStatus}
          />
        )}
      </main>
    </div>
  );
}

function App() {
  return <AppContent />;
}

export default App;
