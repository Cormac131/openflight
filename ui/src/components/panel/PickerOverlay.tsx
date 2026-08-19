import { useState } from 'react';
import { SegmentedControl } from '../ui/SegmentedControl';
import { initialPickerSection, type PickerSection } from './pickerSections';

interface PickerOverlayProps {
  title: string;
  selectedId: string;
  sections: ReadonlyArray<PickerSection>;
  onSelect: (id: string) => void;
  onClose: () => void;
  /** Word-length labels (training implements) use a slightly smaller type size. */
  wide?: boolean;
}

/**
 * Full-screen picker from design doc 6a (`clubsOpen6`): a titled sheet of
 * hairline-bordered option buttons grouped by tab. Every family uses the same
 * 4-across tile size so woods/hybrids match irons.
 */
export function PickerOverlay({ title, selectedId, sections, onSelect, onClose, wide = false }: PickerOverlayProps) {
  const [sectionName, setSectionName] = useState(() => initialPickerSection(sections, selectedId));
  const activeSection = sections.find((section) => section.name === sectionName) ?? sections[0];
  const options = activeSection?.options ?? [];

  return (
    <div
      className={`picker-overlay${wide ? ' picker-overlay--wide' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="picker-overlay__header">
        <span className="picker-overlay__title">{title}</span>
        <button type="button" className="picker-overlay__close" onClick={onClose} aria-label={`Close ${title}`}>
          ✕
        </button>
      </div>
      {sections.length > 1 ? (
        <div className="picker-overlay__tabs">
          <SegmentedControl
            ariaLabel="Groups"
            value={sectionName}
            options={sections.map((section) => ({ id: section.name, label: section.name }))}
            onChange={setSectionName}
          />
        </div>
      ) : null}
      <div className="picker-overlay__body">
        <div className="picker-overlay__grid">
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`picker-overlay__option${
                option.id === selectedId ? ' picker-overlay__option--selected' : ''
              }`}
              aria-pressed={option.id === selectedId}
              onClick={() => onSelect(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
