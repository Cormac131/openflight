import type { ReactNode } from 'react';
import './TabBar.css';

export function TabBar<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: ReadonlyArray<{
    id: T;
    label: string;
    icon?: ReactNode;
    badge?: ReactNode;
    extraClassName?: string;
  }>;
  onChange: (id: T) => void;
}) {
  return (
    <nav className="nav">
      {options.map((option) => {
        const active = option.id === value;
        const classes = ['nav__button'];
        if (active) {
          classes.push('nav__button--active');
        }
        if (option.extraClassName) {
          classes.push(option.extraClassName);
        }

        return (
          <button
            key={option.id}
            type="button"
            className={classes.join(' ')}
            onClick={() => onChange(option.id)}
          >
            {option.icon ? option.icon : null}
            <span>{option.label}</span>
            {option.badge ? option.badge : null}
          </button>
        );
      })}
    </nav>
  );
}
