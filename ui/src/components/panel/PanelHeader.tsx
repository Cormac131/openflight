import type { ReactNode } from 'react';
import { useSystemStore } from '../../stores/useSystemStore';

interface PanelHeaderProps {
  /** Uppercase panel name, e.g. "Live". */
  title: string;
  /** Secondary text after the hairline divider, e.g. the player or shot count. */
  subtitle?: ReactNode;
  /** Right-hand controls: filters, unit label, panel actions. */
  actions?: ReactNode;
  /**
   * Socket connection. Omit to read `useSystemStore`; pass it in tests so SSR
   * is not stuck with the store's initial `false`.
   */
  connected?: boolean;
  /**
   * Invoked when the status dot is tapped. The dot is otherwise decorative, so
   * it carries the Launch Daddy secret tap that used to live on the header logo
   * (the logo now opens the menu sheet).
   */
  onStatusTap?: () => void;
}

/**
 * Page chrome: title plus a connection LED. Green when the socket is up, red
 * when it is down — the same signal that used to live as a System row in the
 * menu sheet.
 */
export function PanelHeader({
  title,
  subtitle,
  actions,
  connected: connectedProp,
  onStatusTap,
}: PanelHeaderProps) {
  const storeConnected = useSystemStore((state) => state.connected);
  const connected = connectedProp ?? storeConnected;
  const status = connected ? 'connected' : 'disconnected';
  const statusLabel = connected ? 'Server connected' : 'Server disconnected';
  const dotClasses = `panel-header__dot panel-header__dot--${status}`;

  return (
    <header className="panel-header">
      <div className="panel-header__identity">
        {onStatusTap ? (
          <button
            type="button"
            className={`${dotClasses} panel-header__dot--tappable`}
            onClick={onStatusTap}
            aria-label={statusLabel}
          />
        ) : (
          <span className={dotClasses} role="status" aria-label={statusLabel} />
        )}
        <span className="panel-header__title">{title}</span>
        {subtitle ? (
          <>
            <span className="panel-header__divider" aria-hidden="true" />
            <span className="panel-header__subtitle">{subtitle}</span>
          </>
        ) : null}
      </div>
      {actions ? <div className="panel-header__actions">{actions}</div> : null}
    </header>
  );
}
