import { useState, useEffect } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useSocket } from './hooks/useSocket';
import { useSystemStore } from './stores/useSystemStore';
import { useShotStore } from './stores/useShotStore';
import { useCameraStore } from './stores/useCameraStore';
import { useDebugStore } from './stores/useDebugStore';
import { usePlayerStore } from './stores/usePlayerStore';
import { useHeroMetricStore } from './stores/useHeroMetricStore';
import { socketService } from './services/socketService';
import { shouldEchoSelectionToServer } from './services/playerSocketSync';
import { DebugPanel } from './components/DebugPanel';
import { DisplayMode } from './components/DisplayMode';
import { SimShotBadges } from './components/SimShotBadges';
import { ShotProcessingArea } from './components/ShotProcessingArea';
import { ShutdownDialog, type ShutdownState } from './components/ShutdownDialog';
import {
  CameraPanel,
  LivePanel,
  MenuSheet,
  PanelFooter,
  PanelHeader,
  PickerOverlay,
  ShotsPanel,
  StatsPanel,
  clubSections,
  trainingImplementSections,
  type PanelView,
} from './components/panel';
import { ALL_CLUBS } from './data/clubs';
import { getTrainingImplementLabel } from './data/trainingImplements';
import { unlockAudioCue } from './utils/audioCue';
import {
  useLaunchDaddy,
  LaunchDaddyOverlay,
  LaunchDaddyBrand,
  LaunchDaddySecretIndicator,
} from './components/LaunchDaddy';

import './App.css';
import './components/panel/panel.css';

function clubLabel(clubId: string): string {
  return ALL_CLUBS.find((club) => club.id === clubId)?.label ?? clubId.toUpperCase();
}

function AppContent() {
  const { shutdown } = useSocket();
  const { connected, mockMode, debugMode, latestSimShots, serverClub } = useSystemStore(
    useShallow((state) => ({
      connected: state.connected,
      mockMode: state.mockMode,
      debugMode: state.debugMode,
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
  const { selectedPlayer, selectPlayer } = usePlayerStore(
    useShallow((state) => ({ selectedPlayer: state.selectedPlayer, selectPlayer: state.selectPlayer }))
  );
  const serverPlayerName = useSystemStore((state) => state.serverPlayerName);
  const { heroMetricId, setHeroMetricId } = useHeroMetricStore(
    useShallow((state) => ({ heroMetricId: state.heroMetricId, setHeroMetricId: state.setHeroMetricId }))
  );
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

  const [currentView, setCurrentView] = useState<PanelView>('live');
  const [selectedClub, setSelectedClub] = useState('driver');
  const [selectedTrainingImplement, setSelectedTrainingImplement] = useState('driver');
  const [menuOpen, setMenuOpen] = useState(false);
  const [showShutdown, setShowShutdown] = useState(false);
  const [shutdownState, setShutdownState] = useState<ShutdownState>('confirm');
  // Open on every app load so the user confirms their club before the first
  // shot; dismissing keeps the default. The /display route returns early below,
  // so this never appears in the passive TV view.
  const [pickerOpen, setPickerOpen] = useState(true);

  // Reflect a server-pushed club change (e.g. the club changed in the connected
  // simulator) locally without echoing back. Done during render (React's "adjust
  // state when an input changes" pattern) rather than in an effect.
  const [appliedServerClub, setAppliedServerClub] = useState<string | null>(null);
  if (serverClub && serverClub !== appliedServerClub) {
    setAppliedServerClub(serverClub);
    setSelectedClub(serverClub);
  }
  // Same pattern for a server-pushed player change. This used to live in
  // PlayerPicker, which the menu sheet replaced.
  const [appliedServerPlayer, setAppliedServerPlayer] = useState<string | null>(null);
  if (serverPlayerName && serverPlayerName !== appliedServerPlayer) {
    setAppliedServerPlayer(serverPlayerName);
    if (serverPlayerName !== selectedPlayer) {
      selectPlayer(serverPlayerName);
    }
  }

  const { isLaunchDaddyMode, isExploding, triggerExplosion, handleSecretTap } = useLaunchDaddy();
  const isDisplayRoute = typeof window !== 'undefined' && window.location.pathname.replace(/\/$/, '') === '/display';
  const isSwingSpeedMode = triggerStatus.mode === 'swing-speed';
  const activeImplementLabel = isSwingSpeedMode
    ? getTrainingImplementLabel(selectedTrainingImplement)
    : clubLabel(selectedClub);

  // Push the local player to the server once connected, so a reload restores it.
  // Do not re-emit when selectedPlayer changes: that echoes player_changed back
  // as set_player and races with the connect-time session_state snapshot.
  useEffect(() => {
    if (!connected || !shouldEchoSelectionToServer('became-connected')) return;
    socketService.setPlayer(usePlayerStore.getState().selectedPlayer);
  }, [connected]);

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

  const handlePickerSelect = (id: string) => {
    if (isSwingSpeedMode) {
      setSelectedTrainingImplement(id);
      socketService.setTrainingImplement(id);
    } else {
      setSelectedClub(id);
      socketService.setClub(id);
    }
    setPickerOpen(false);
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

  const changeAction = (
    <button
      type="button"
      className="panel-action"
      onClick={() => {
        setPickerOpen(true);
      }}
    >
      {isSwingSpeedMode ? 'Change implement' : 'Change club'}
      <span className="panel-action__value">{activeImplementLabel}</span>
    </button>
  );

  const footerAction =
    currentView === 'stats' ? (
      <button type="button" className="panel-action panel-action--danger" onClick={() => socketService.clearSession()}>
        Clear session
      </button>
    ) : currentView === 'debug' ? (
      <button type="button" className="panel-action panel-action--ghost" onClick={() => socketService.toggleDebug()}>
        {debugMode ? 'Stop recording' : 'Record'}
      </button>
    ) : (
      changeAction
    );

  return (
    <div className={`panel-app ${isLaunchDaddyMode ? 'app--launch-daddy' : ''} ${isExploding ? 'app--exploding' : ''}`}>
      <LaunchDaddyOverlay />
      <LaunchDaddySecretIndicator />

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

      <main className="panel-app__main">
        {currentView === 'live' && (
          <>
            {isNewShot && <div key={shotVersion} className="shot-flash" />}
            <ShotProcessingArea phase={shotProcessingPhase}>
              <LivePanel
                key={shotVersion}
                shot={latestShot}
                shots={shots}
                playerName={selectedPlayer}
                clubLabel={activeImplementLabel}
                activeTrainingImplement={isSwingSpeedMode ? selectedTrainingImplement : undefined}
                onStatusTap={handleSecretTap}
                heroMetricId={heroMetricId}
                onPromoteMetric={setHeroMetricId}
              />
            </ShotProcessingArea>
            {debugMode && <SimShotBadges latestSimShots={latestSimShots} />}
          </>
        )}
        {currentView === 'stats' && <StatsPanel shots={shots} activeClub={selectedClub} playerName={selectedPlayer} />}
        {currentView === 'shots' && (
          <ShotsPanel
            shots={shots}
            playerName={selectedPlayer}
            onDeleteShot={(timestamp) => socketService.deleteShot(timestamp)}
          />
        )}
        {currentView === 'camera' && (
          <CameraPanel
            cameraStatus={cameraStatus}
            onToggleCamera={() => socketService.toggleCamera()}
            onToggleStream={() => socketService.toggleCameraStream()}
          />
        )}
        {currentView === 'debug' && (
          <div className="panel">
            <PanelHeader title="Debug" subtitle={debugMode ? 'Recording' : 'Idle'} />
            <div className="panel__body panel-app__debug">
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
            </div>
          </div>
        )}
      </main>

      {/*
       * Overlays sit outside <main> so they cover the footer too, matching how
       * 6a draws them over the whole card.
       */}
      {menuOpen ? (
        <MenuSheet
          onClose={() => setMenuOpen(false)}
          onShutdown={() => {
            setMenuOpen(false);
            setShutdownState('confirm');
            setShowShutdown(true);
          }}
        />
      ) : null}

      {pickerOpen ? (
        <PickerOverlay
          title={isSwingSpeedMode ? 'Select implement' : 'Select club'}
          selectedId={isSwingSpeedMode ? selectedTrainingImplement : selectedClub}
          sections={isSwingSpeedMode ? trainingImplementSections() : clubSections()}
          onSelect={handlePickerSelect}
          onClose={() => setPickerOpen(false)}
          wide={isSwingSpeedMode}
        />
      ) : null}

      <PanelFooter
        currentView={currentView}
        onChangeView={setCurrentView}
        onOpenMenu={() => setMenuOpen((open) => !open)}
        menuOpen={menuOpen}
        action={
          <>
            {mockMode ? (
              <button
                type="button"
                className="panel-action panel-action--ghost"
                onClick={() => socketService.simulateShot()}
              >
                {isSwingSpeedMode ? 'Simulate swing' : 'Simulate shot'}
              </button>
            ) : null}
            {footerAction}
          </>
        }
        shotCount={shots.length}
        cameraStreaming={cameraStatus.streaming}
        ballDetected={cameraStatus.ball_detected}
        debugRecording={debugMode}
        brand={isLaunchDaddyMode ? <LaunchDaddyBrand /> : undefined}
      />
    </div>
  );
}

function App() {
  return <AppContent />;
}

export default App;
