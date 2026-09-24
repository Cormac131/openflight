import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n';

const LETTER_ROWS = [
  ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
  ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'],
  ['Z', 'X', 'C', 'V', 'B', 'N', 'M'],
] as const;

/** Profile names: digits plus the few separators a name needs. */
const NAME_SYMBOL_ROWS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['-', "'", '.', '_'],
] as const;

/** Passwords: every printable ASCII symbol (WPA passphrases allow all of them). */
const TEXT_SYMBOL_ROWS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['!', '@', '#', '$', '%', '^', '&', '*', '(', ')'],
  ['-', '_', '=', '+', '[', ']', '{', '}', ';', ':'],
  ["'", '"', ',', '.', '<', '>', '/', '?', '\\', '|', '`', '~'],
] as const;

export type KeyboardLayout = 'name' | 'text';

interface OnScreenKeyboardProps {
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  /** `name`: auto-capitalise words. `text`: full ASCII, shift acts as caps lock. */
  layout?: KeyboardLayout;
}

/**
 * Touch keyboard for the kiosk (no physical keyboard, native OSK suppressed
 * with `inputMode="none"` on the paired input).
 */
export function OnScreenKeyboard({ value, onChange, maxLength, layout = 'name' }: OnScreenKeyboardProps) {
  const { t } = useI18n();
  const autoCapitalize = layout === 'name';
  const [shifted, setShifted] = useState(autoCapitalize);
  const [symbols, setSymbols] = useState(false);
  const rows = symbols ? (layout === 'name' ? NAME_SYMBOL_ROWS : TEXT_SYMBOL_ROWS) : LETTER_ROWS;

  const append = (chunk: string) => {
    const room = maxLength - value.length;
    if (room <= 0) return;
    onChange(value + chunk.slice(0, room));
  };

  const insertChar = (raw: string) => {
    const isLetter = /^[a-z]$/i.test(raw);
    append(isLetter ? (shifted ? raw.toUpperCase() : raw.toLowerCase()) : raw);
    if (isLetter && shifted && autoCapitalize) setShifted(false);
  };

  const insertSpace = () => {
    append(' ');
    if (autoCapitalize) setShifted(true);
  };

  return (
    <div className="osk" role="group" aria-label={t('keyboard.aria')}>
      {rows.map((row) => (
        <div className="osk__row" key={row.join('')}>
          {row.map((key) => {
            const label = /^[A-Z]$/.test(key) && !shifted ? key.toLowerCase() : key;
            return (
              <button key={key} type="button" className="osk__key" onClick={() => insertChar(key)}>
                {layout === 'name' ? key : label}
              </button>
            );
          })}
        </div>
      ))}
      <div className="osk__row">
        {symbols ? (
          <button
            type="button"
            className="osk__key osk__key--mod"
            aria-label={t('keyboard.letters')}
            onClick={() => setSymbols(false)}
          >
            ABC
          </button>
        ) : (
          <>
            <button
              type="button"
              className={`osk__key osk__key--mod${shifted ? ' osk__key--active' : ''}`}
              aria-label={t('keyboard.shift')}
              aria-pressed={shifted}
              onClick={() => setShifted((on) => !on)}
            >
              ⇧
            </button>
            <button
              type="button"
              className="osk__key osk__key--mod"
              aria-label={t('keyboard.numbers')}
              onClick={() => setSymbols(true)}
            >
              123
            </button>
          </>
        )}
        <button
          type="button"
          className="osk__key osk__key--wide"
          aria-label={t('keyboard.space')}
          onClick={insertSpace}
        />
        <button
          type="button"
          className="osk__key osk__key--mod"
          aria-label={t('keyboard.backspace')}
          onClick={() => onChange(value.slice(0, -1))}
        >
          ⌫
        </button>
      </div>
    </div>
  );
}
