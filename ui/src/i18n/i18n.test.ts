import { afterEach, describe, expect, it } from 'vitest';
import { catalogs, LOCALES, setActiveLocale, t } from './index';
import { en } from './en';

describe('i18n catalogs', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it('ships English, Spanish, French, and Portuguese', () => {
    expect(LOCALES.map((locale) => locale.id)).toEqual(['en', 'es', 'fr', 'pt']);
  });

  it('keeps every locale in lockstep with English keys', () => {
    const englishKeys = Object.keys(en).sort();
    expect(englishKeys.length).toBeGreaterThan(80);

    for (const [id, messages] of Object.entries(catalogs)) {
      expect(Object.keys(messages).sort(), id).toEqual(englishKeys);
    }
  });

  it('interpolates placeholders in the active locale', () => {
    expect(t('header.shotCount', { n: '03' })).toBe('Shot 03');

    setActiveLocale('es');
    expect(t('nav.live')).toBe('En vivo');
    expect(t('header.shotCount', { n: '03' })).toBe('Golpe 03');
  });

  it('falls back to English when a locale id is unknown', () => {
    setActiveLocale('de');
    expect(t('nav.live')).toBe('Live');
  });
});
