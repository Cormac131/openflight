import { CLUBS_BY_TYPE } from '../../data/clubs';
import { getTrainingImplementsByGroup } from '../../data/trainingImplements';

export interface PickerOption {
  id: string;
  label: string;
}

export interface PickerSection {
  name: string;
  options: ReadonlyArray<PickerOption>;
}

/** Club sections for the picker, in the order `clubs.ts` declares them. */
export function clubSections(): PickerSection[] {
  return Object.entries(CLUBS_BY_TYPE).map(([name, clubs]) => ({ name, options: clubs }));
}

/** Training-implement sections, used in place of clubs during swing-speed mode. */
export function trainingImplementSections(): PickerSection[] {
  return Object.entries(getTrainingImplementsByGroup()).map(([name, items]) => ({
    name,
    options: items.map((item) => ({ id: item.id, label: item.label })),
  }));
}

/** Open on the family that already contains the selection (driver → Woods). */
export function initialPickerSection(
  sections: ReadonlyArray<PickerSection>,
  selectedId: string,
): string {
  const match = sections.find((section) => section.options.some((option) => option.id === selectedId));
  return match?.name ?? sections[0]?.name ?? '';
}
