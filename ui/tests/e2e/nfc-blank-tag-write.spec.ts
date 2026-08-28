import { test } from '@playwright/test';
import type { Page } from '@playwright/test';
import {
  clubTags,
  expect,
  gotoApp,
  presentTag,
  resetClubTags,
  resetSession,
  setClub,
  withControlSocket,
} from './helpers';

/*
 * The blank-tag write flow end to end: a tag with nothing on it is offered for
 * writing, the club is confirmed, and it is written onto the tag itself. The
 * mock reader keeps simulated tag memory, so a tag written here reads its club
 * back on the next tap exactly as hardware would.
 */

const BLANK_TAG = '04B1C2D3';
const SECOND_BLANK_TAG = '04B1C2E4';

async function dismissPicker(page: Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

async function presentBlank(uid: string) {
  await withControlSocket((socket) => presentTag(socket, uid, { blank: true, writable: true }));
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

test('offers to write a blank tag rather than only recording it', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);

  await presentBlank(BLANK_TAG);

  await expect(page.getByRole('dialog', { name: 'Blank tag' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Blank tag' })).toContainText('04:B1:C2:D3');
  // The learn-by-UID prompt is for tags that cannot be written.
  await expect(page.getByRole('dialog', { name: 'New club tag' })).toBeHidden();
});

test('asks for confirmation before committing a club to the tag', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await presentBlank(BLANK_TAG);

  await page.getByRole('button', { name: 'Irons' }).click();
  await page.getByRole('button', { name: '7i', exact: true }).click();

  await expect(page.getByRole('dialog')).toContainText('Write 7 Iron to this tag?');
  // Nothing is written until the confirmation is taken.
  expect(await withControlSocket(clubTags)).toEqual([]);
});

test('writes the club onto the tag and selects it', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await presentBlank(BLANK_TAG);

  await page.getByRole('button', { name: 'Irons' }).click();
  await page.getByRole('button', { name: '7i', exact: true }).click();
  await page.getByRole('button', { name: 'Write tag' }).click();

  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.locator('.panel-header__club', { hasText: '7 Iron' })).toBeVisible();
  expect(await withControlSocket(clubTags)).toEqual([expect.objectContaining({ uid: BLANK_TAG, club: '7-iron' })]);
});

test('the written tag then carries its own club on the next tap', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await presentBlank(BLANK_TAG);
  await page.getByRole('button', { name: 'Woods' }).click();
  await page.getByRole('button', { name: '3W', exact: true }).click();
  await page.getByRole('button', { name: 'Write tag' }).click();
  await expect(page.locator('.panel-header__club', { hasText: '3 Wood' })).toBeVisible();

  await withControlSocket((socket) => setClub(socket, 'driver'));
  await expect(page.locator('.panel-header__club', { hasText: 'Driver' })).toBeVisible();
  // No blank/writable hint this time: the club comes off the tag's own memory.
  const scan = await withControlSocket((socket) => presentTag(socket, BLANK_TAG));

  expect(scan).toMatchObject({ club: '3-wood', source: 'tag' });
  await expect(page.getByRole('dialog', { name: 'Blank tag' })).toBeHidden();
  await expect(page.locator('.panel-header__club', { hasText: '3 Wood' })).toBeVisible();
});

test('can be abandoned at the club picker without writing', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await presentBlank(BLANK_TAG);

  await page.getByRole('button', { name: 'Close Blank tag' }).click();

  await expect(page.getByRole('dialog', { name: 'Blank tag' })).toBeHidden();
  expect(await withControlSocket(clubTags)).toEqual([]);
});

test('can be abandoned at the confirmation without writing', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);
  await presentBlank(SECOND_BLANK_TAG);

  await page.getByRole('button', { name: 'Irons' }).click();
  await page.getByRole('button', { name: 'PW', exact: true }).click();
  // exact: the dismiss scrim is labelled "Cancel writing the tag".
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();

  await expect(page.getByRole('dialog')).toBeHidden();
  expect(await withControlSocket(clubTags)).toEqual([]);
  await expect(page.locator('.panel-header__club', { hasText: 'Driver' })).toBeVisible();
});
