import type { MessageKey } from '../i18n/useI18n';
import type { ReleaseChannel, ReleaseInfo } from '../types/release';

const CHANNEL_LABEL_KEYS: Record<ReleaseChannel, MessageKey> = {
  stable: 'menu.channelStable',
  experimental: 'menu.channelExperimental',
  source: 'menu.channelSource',
};

/** One-line build identity for the menu, e.g. `0.3.0-dev.42 · Experimental`. */
export function releaseVersionLabel(info: ReleaseInfo | null, t: (key: MessageKey) => string): string {
  if (!info) {
    return t('menu.unavailable');
  }
  return `${info.version} · ${t(CHANNEL_LABEL_KEYS[info.channel])}`;
}
