import { describe, expect, it } from 'vitest';
import { clubSections, initialPickerSection, trainingImplementSections } from './pickerSections';

describe('initialPickerSection', () => {
  const clubs = clubSections();

  it('opens Woods when the driver is selected', () => {
    expect(initialPickerSection(clubs, 'driver')).toBe('Woods');
  });

  it('opens Irons when a wedge is selected', () => {
    expect(initialPickerSection(clubs, 'pw')).toBe('Irons');
  });

  it('falls back to the first section when the id is unknown', () => {
    expect(initialPickerSection(clubs, 'not-a-club')).toBe('Irons');
  });

  it('opens the matching training group', () => {
    const groups = trainingImplementSections();
    expect(initialPickerSection(groups, 'stack-160g')).toBe('TheStack');
  });
});
