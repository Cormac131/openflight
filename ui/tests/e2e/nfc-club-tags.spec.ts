import { test } from '@playwright/test';
import type { Page } from '@playwright/test';
import {
  clubTags,
  expect,
  gotoApp,
  presentUnwritableTag,
  resetClubTags,
  resetSession,
  setClub,
  withControlSocket,
} from './helpers';

/*
 * Drives the real backend's mock NFC reader over the control socket, so these
 * cover the whole path a tapped club takes: reader thread -> registry -> socket
 * broadcast -> kiosk. The unit tests cover each piece; only this level shows
 * that a tap actually moves the club on screen.
 */

// Distinct per test: the service suppresses a repeat of the same UID for three
// seconds, so sharing one UID would make tests wait on each other.
const IRON_TAG = '04A2B1C3';
const DRIVER_TAG = '04A2B1D4';
const WEDGE_TAG = '04A2B1E5';
const SECOND_TAG = '04A2B1F6';

async function dismissPicker(page: Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

/** Tap an unlearned tag and teach it a club through the kiosk prompt. */
async function learnTag(page: Page, uid: string, section: string, tile: string) {
  await withControlSocket((socket) => presentUnwritableTag(socket, uid));
  await expect(page.getByRole('dialog', { name: 'New club tag' })).toBeVisible();
  await page.getByRole('button', { name: section }).click();
  await page.getByRole('button', { name: tile, exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'New club tag' })).toBeHidden();
}

test.beforeEach(async () => {
  await withControlSocket(async (socket) => {
    await resetSession(socket);
    await resetClubTags(socket);
    await setClub(socket, 'driver');
  });
});

test.afterAll(async () => {
  await withControlSocket(resetClubTags);
});

test('asks which club an unrecognized tag belongs to, showing its UID', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);

  await withControlSocket((socket) => presentUnwritableTag(socket, IRON_TAG));

  const prompt = page.getByRole('dialog', { name: 'New club tag' });
  await expect(prompt).toBeVisible();
  await expect(prompt.getByText('04:A2:B1:C3')).toBeVisible();
  // Nothing is preselected: a mis-tap here would be written to disk.
  await expect(prompt.locator('.picker-overlay__option--selected')).toHaveCount(0);
});

test('learning a tag selects that club and persists it', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);

  await learnTag(page, IRON_TAG, 'Irons', '7i');

  await expect(page.locator('.panel-header__club', { hasText: '7 Iron' })).toBeVisible();
  const tags = await withControlSocket(clubTags);
  expect(tags).toEqual([expect.objectContaining({ uid: IRON_TAG, club: '7-iron' })]);
});

test('a learned tag survives a reload and still selects its club', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await learnTag(page, DRIVER_TAG, 'Irons', '9i');

  await gotoApp(page);
  await dismissPicker(page);
  await withControlSocket((socket) => setClub(socket, 'driver'));
  await expect(page.locator('.panel-header__club', { hasText: 'Driver' })).toBeVisible();

  await withControlSocket((socket) => presentUnwritableTag(socket, DRIVER_TAG));

  const prompt = page.getByRole('dialog', { name: 'Club tag' });
  await expect(prompt).toBeVisible();
  await expect(prompt.getByRole('button', { name: 'Forget the tag for 9 Iron' })).toBeVisible();
  await expect(page.locator('.panel-header__club', { hasText: '9 Iron' })).toBeVisible();
});

test('shows a large confirmation naming the club, then clears it', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await learnTag(page, WEDGE_TAG, 'Irons', 'PW');
  await withControlSocket((socket) => setClub(socket, 'driver'));

  await withControlSocket((socket) => presentUnwritableTag(socket, WEDGE_TAG));

  const toast = page.getByRole('status');
  await expect(toast).toBeVisible();
  await expect(toast).toContainText('Club selected');
  await expect(toast).toContainText('Pitching Wedge');
  // Informational only: it must never intercept a tap while it fades.
  await expect(toast).toHaveCSS('pointer-events', 'none');
  await expect(toast).toBeHidden({ timeout: 5000 });
});

test('a tag tap closes the club picker it just answered', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await learnTag(page, SECOND_TAG, 'Woods', '3W');

  await page.locator('.panel-header').getByRole('button', { name: 'Change club' }).click();
  await expect(page.getByRole('dialog', { name: 'Select club' })).toBeVisible();

  await withControlSocket((socket) => setClub(socket, 'driver'));
  await withControlSocket((socket) => presentUnwritableTag(socket, SECOND_TAG));

  await expect(page.getByRole('dialog', { name: 'Select club' })).toBeHidden();
  await expect(page.locator('.panel-header__club', { hasText: '3 Wood' })).toBeVisible();
});

test('forgets a learned tag from the scan dialog, not the menu', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await learnTag(page, IRON_TAG, 'Irons', '7i');

  await page.getByRole('button', { name: 'Open menu' }).click();
  const menu = page.getByRole('dialog', { name: 'Menu' });
  await expect(menu.getByText('Club tags')).toHaveCount(0);
  await expect(menu.getByRole('button', { name: /Forget/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Close menu' }).click();

  await withControlSocket((socket) => presentUnwritableTag(socket, IRON_TAG));
  const prompt = page.getByRole('dialog', { name: 'Club tag' });
  await expect(prompt).toBeVisible();
  await prompt.getByRole('button', { name: 'Forget the tag for 7 Iron' }).click();

  await expect(page.getByRole('dialog', { name: 'New club tag' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Forget/ })).toHaveCount(0);
  expect(await withControlSocket(clubTags)).toEqual([]);
});

test('a forgotten tag prompts to be learned again', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await learnTag(page, IRON_TAG, 'Irons', '7i');

  await withControlSocket(resetClubTags);
  await withControlSocket((socket) => presentUnwritableTag(socket, IRON_TAG));

  await expect(page.getByRole('dialog', { name: 'New club tag' })).toBeVisible();
});
