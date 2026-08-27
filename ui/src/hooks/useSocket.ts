import { useEffect, useCallback } from 'react';
import { socketService } from '../services/socketService';

export function useSocket() {
  useEffect(() => {
    socketService.connect();
  }, []);

  const shutdown = useCallback(async () => {
    const response = await fetch('/api/shutdown', { method: 'POST' });
    if (!response.ok) {
      throw new Error(`Shutdown request failed (${response.status})`);
    }
  }, []);

  const applyUpdateNow = useCallback(async () => {
    const response = await fetch('/api/update/apply-now', { method: 'POST' });
    if (!response.ok) {
      throw new Error(`Apply-now request failed (${response.status})`);
    }
  }, []);

  const skipUpdate = useCallback(async (tag: string) => {
    const response = await fetch('/api/update/skip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag }),
    });
    if (!response.ok) {
      throw new Error(`Skip-update request failed (${response.status})`);
    }
  }, []);

  return { shutdown, applyUpdateNow, skipUpdate };
}
