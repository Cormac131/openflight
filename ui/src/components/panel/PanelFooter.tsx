import type { ReactNode } from 'react';
import Logo from '../../logo/Logo';
import { TabBar } from '../ui/TabBar';
import type { PanelView } from './views';
import { PANEL_VIEWS } from './views';

interface PanelFooterProps {
  currentView: PanelView;
  onChangeView: (view: PanelView) => void;
  onOpenMenu: () => void;
  menuOpen: boolean;
  /** Right-hand action for the active panel (change club, clear session, …). */
  action?: ReactNode;
  shotCount: number;
  cameraStreaming: boolean;
  ballDetected: boolean;
  debugRecording: boolean;
  /** Replaces the logo when Launch Daddy mode is active. */
  brand?: ReactNode;
}

/**
 * Bottom bar from design doc 6a: menu button, text nav tabs, panel action.
 *
 * The mockup footer shows four tabs; Debug is kept as a fifth because it is a
 * working diagnostic tool and burying it behind a gesture makes it unreachable
 * on the kiosk touchscreen.
 */
export function PanelFooter({
  currentView,
  onChangeView,
  onOpenMenu,
  menuOpen,
  action,
  shotCount,
  cameraStreaming,
  ballDetected,
  debugRecording,
  brand,
}: PanelFooterProps) {
  const options = PANEL_VIEWS.map((view) => {
    switch (view.id) {
      case 'shots':
        return {
          ...view,
          badge: shotCount > 0 ? <span className="nav__badge">{shotCount}</span> : undefined,
        };
      case 'camera':
        return {
          ...view,
          extraClassName: cameraStreaming ? 'nav__button--streaming' : undefined,
          badge: ballDetected ? <span className="nav__ball-dot" /> : undefined,
        };
      case 'debug':
        return {
          ...view,
          extraClassName: debugRecording ? 'nav__button--recording' : undefined,
          badge: debugRecording ? <span className="nav__recording-dot" /> : undefined,
        };
      default:
        return view;
    }
  });

  return (
    <div className="panel-footer">
      <button
        type="button"
        className="panel-footer__menu"
        style={{ border: 'none' }}
        onClick={onOpenMenu}
        aria-expanded={menuOpen}
        aria-label="Open menu"
      >
        {brand ?? <Logo size="small" variant="mono" />}
      </button>

      <TabBar
        className="panel-footer__tabs"
        ariaLabel="Panels"
        value={currentView}
        onChange={onChangeView}
        options={options}
      />

      {action ? <div className="panel-footer__action">{action}</div> : null}
    </div>
  );
}
