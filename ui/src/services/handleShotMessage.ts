import type { SessionStats, Shot } from '../types/shot';
import { useShotStore } from '../stores/useShotStore';
import { playSwingCapturedCue } from '../utils/audioCue';

export interface ShotMessage {
  shot: Shot;
  stats: SessionStats;
  pending?: {
    iwr6843?: boolean;
  };
}

export function handleShotMessage(data: ShotMessage) {
  const shotStore = useShotStore.getState();
  shotStore.addShot(data.shot);
  if (data.pending?.iwr6843) {
    shotStore.startShotProcessing('iwr_dump', data.shot.timestamp);
  }
  playSwingCapturedCue();
}

export function handleShotUpdate(data: { shot: Shot; stats: SessionStats }) {
  useShotStore.getState().updateShot(data.shot);
}
