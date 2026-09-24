/** Small stroke icons sized in em so they follow the surrounding type. */

export function WifiIcon({ bars, off = false }: { bars: number; off?: boolean }) {
  const arcs = [
    'M12 18.5h.01',
    'M9 15.2a4.2 4.2 0 0 1 6 0',
    'M6.2 12.3a8.2 8.2 0 0 1 11.6 0',
    'M3.3 9.3a12.3 12.3 0 0 1 17.4 0',
  ];
  return (
    <svg className="conn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
      {arcs.map((d, index) => (
        <path key={d} d={d} className={index < Math.max(bars, 1) && !off ? 'conn-icon__on' : 'conn-icon__dim'} />
      ))}
      {off ? <path d="M4 4l16 16" className="conn-icon__on" /> : null}
    </svg>
  );
}

export function BluetoothIcon({ off = false }: { off?: boolean }) {
  return (
    <svg className="conn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
      <path d="M7 7l10 10-5 4V3l5 4L7 17" className={off ? 'conn-icon__dim' : 'conn-icon__on'} />
      {off ? <path d="M4 4l16 16" className="conn-icon__on" /> : null}
    </svg>
  );
}

export function LockIcon() {
  return (
    <svg
      className="conn-icon conn-icon--small"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      aria-hidden="true"
    >
      <rect x="5" y="11" width="14" height="10" rx="2" className="conn-icon__on" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" className="conn-icon__on" />
    </svg>
  );
}
