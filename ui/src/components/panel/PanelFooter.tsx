import type { ReactNode } from 'react';
import Logo from '../../logo/Logo';
import { TabBar } from '../ui/TabBar';
import type { PanelView } from './views';
import { PANEL_VIEWS } from './views';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';

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
  const { t } = useI18n();
  const options = PANEL_VIEWS.map((view) => {
    const label = t(`nav.${view.id}` as MessageKey);
    switch (view.id) {
      case 'shots':
        return {
          ...view,
          label,
          badge: shotCount > 0 ? <span className="nav__badge">{shotCount}</span> : undefined,
        };
      case 'camera':
        return {
          ...view,
          label,
          extraClassName: cameraStreaming ? 'nav__button--streaming' : undefined,
          badge: ballDetected ? <span className="nav__ball-dot" /> : undefined,
        };
      case 'debug':
        return {
          ...view,
          label,
          extraClassName: debugRecording ? 'nav__button--recording' : undefined,
          badge: debugRecording ? <span className="nav__recording-dot" /> : undefined,
        };
      default:
        return { ...view, label };
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
        aria-label={t('nav.openMenu')}
      >
        {brand ?? <Logo size="small" variant="mono" />}
      </button>

      <TabBar
        className="panel-footer__tabs"
        ariaLabel={t('nav.panels')}
        value={currentView}
        onChange={onChangeView}
        options={options}
      />

      {action ? <div className="panel-footer__action">{action}</div> : null}
    </div>
  );
}
