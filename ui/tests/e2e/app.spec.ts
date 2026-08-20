import { test } from '@playwright/test';
import { expect, gotoApp, setClub, simulateShot, withControlSocket } from './helpers';

/** Dismiss the club picker that opens on every load, keeping the default club. */
async function dismissPicker(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
}

/** Open the footer menu sheet (player / units / theme / system / shut down). */
async function openMenu(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Open menu' }).click();
}

test.beforeEach(async () => {
  await withControlSocket(async (socket) => {
    socket.emit('clear_session');
    await new Promise<void>((resolve) => {
      socket.once('session_cleared', () => resolve());
    });
    await setClub(socket, 'driver');
  });
});

test('stays usable when websocket upgrade fails and socket.io falls back to polling', async ({ page }) => {
  await gotoApp(page);

  // The club picker is the first thing shown, and it only renders once mounted.
  await expect(page.getByRole('dialog', { name: 'Select club' })).toBeVisible();

  await dismissPicker(page);
  await expect(page.getByLabel('Server connected')).toBeVisible();
});

test('supports club selection choose and dismiss flows against mock backend', async ({ page }) => {
  await gotoApp(page);

  await page.getByRole('button', { name: 'Irons' }).click();
  await page.getByRole('button', { name: '7i', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Select club' })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Change club' })).toBeVisible();
  await expect(page.locator('.panel-header__club', { hasText: '7 Iron' })).toBeVisible();

  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await page.getByRole('button', { name: 'Shots' }).click();
  await expect(page.locator('.shots-panel__row')).toHaveCount(1);
  await expect(page.getByText('7-iron')).toBeVisible();

  await page.reload();
  await expect(page.getByRole('dialog', { name: 'Select club' })).toBeVisible();
  await dismissPicker(page);
  // Dismissing keeps whatever the server last reported, not a reset to driver.
  await expect(page.getByRole('button', { name: 'Change club' })).toBeVisible();
  await expect(page.locator('.panel-header__club', { hasText: '7 Iron' })).toBeVisible();
});

test('renders live shot data and mock-mode simulate flow', async ({ page }) => {
  await gotoApp(page);
  await dismissPicker(page);

  await expect(page.getByRole('button', { name: 'Simulate shot' })).toBeVisible();
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Simulate shot' }).click();

  await expect(page.getByText('Ready', { exact: true })).toBeHidden();
  await expect(page.locator('.live-panel__spotlight-value')).not.toHaveText('—');
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);

  await page.getByRole('button', { name: 'Hide shot overlay' }).click();
  await expect(page.locator('.live-panel__spotlight')).toHaveCount(0);
});

test('does not keep the previous metric title yellow after selecting a new hero', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await gotoApp(page);
  await dismissPicker(page);

  await page.locator('.metric-card--interactive').filter({ hasText: 'Club speed' }).click();

  await expect(page.locator('.metric-card--selected')).toContainText('Club speed');
  await expect(page.locator('.metric-card--selected .metric-card__label')).toHaveCSS('color', 'rgb(255, 212, 0)');
  await expect(
    page.locator('.metric-card--interactive').filter({ hasText: 'Ball speed' }).locator('.metric-card__label')
  ).not.toHaveCSS('color', 'rgb(255, 212, 0)');
});

test('pins a tapped metric top-left and remembers it', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await gotoApp(page);
  await dismissPicker(page);

  await expect(page.locator('.metric-card--selected')).toContainText('Ball speed');
  await expect(page.locator('.live-panel__spotlight')).toHaveCount(0);

  await page.locator('.metric-card--interactive').filter({ hasText: 'Club speed' }).click();

  await expect(page.locator('.metric-card--selected')).toContainText('Club speed');
  await expect(page.locator('.live-panel__grid .metric-card').first()).toContainText('Club speed');
  await expect(page.locator('.live-panel__grid .metric-card')).toHaveCount(10);
  await expect(page.locator('.live-panel__grid')).toContainText('Ball speed');

  await page.reload();
  await dismissPicker(page);
  await expect(page.locator('.metric-card--selected')).toContainText('Club speed');
  await expect(page.locator('.live-panel__grid .metric-card').first()).toContainText('Club speed');
});

test('switches between primary navigation views', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
    await setClub(socket, '7-iron');
    await simulateShot(socket);
  });

  await gotoApp(page);
  await dismissPicker(page);

  await page.getByRole('button', { name: 'Stats' }).click();
  await expect(page.getByText('Avg ball')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Clear session' })).toBeVisible();

  await page.getByRole('button', { name: 'Shots' }).click();
  await expect(page.locator('.shots-panel__row')).toHaveCount(2);
  await expect(page.getByText('7-iron')).toBeVisible();

  await page.getByRole('button', { name: 'Camera' }).click();
  await expect(page.getByText('Camera unavailable')).toBeVisible();

  await page.getByRole('button', { name: 'Debug' }).click();
  await expect(page.getByRole('heading', { name: 'System Status' })).toBeVisible();
  await expect(page.getByText('mock')).toBeVisible();

  await page.getByRole('button', { name: 'Live' }).click();
  await expect(page.getByRole('button', { name: 'Simulate shot' })).toBeVisible();
});

test('scrolls the shots list by dragging on a row', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });

  await withControlSocket(async (socket) => {
    for (let i = 0; i < 16; i += 1) {
      await simulateShot(socket);
    }
  });

  await gotoApp(page);
  await dismissPicker(page);
  await page.getByRole('button', { name: 'Shots' }).click();

  const list = page.getByRole('region', { name: 'Recorded shots' });
  await expect(list.locator('.shots-panel__row')).toHaveCount(16);

  const before = await list.evaluate((node) => node.scrollTop);
  const box = await list.boundingBox();
  expect(box).toBeTruthy();

  await page.mouse.move(box!.x + box!.width / 2, box!.y + 180);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 40, { steps: 12 });
  await page.mouse.up();

  await expect.poll(async () => list.evaluate((node) => node.scrollTop)).toBeGreaterThan(before);
  await expect(page.locator('.shots-panel__validation')).toHaveCount(0);
});

test('expands a shot row to reveal its validation fields', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await gotoApp(page);
  await dismissPicker(page);
  await page.getByRole('button', { name: 'Shots' }).click();

  // 7b's row has no room for the fields inline, so they live behind a tap.
  await expect(page.locator('.shots-panel__validation')).toHaveCount(0);

  await page.locator('.shots-panel__row-main').first().click();

  await expect(page.locator('.shots-panel__validation')).toBeVisible();
  await expect(page.getByPlaceholder('mph')).toBeVisible();
  await expect(page.getByPlaceholder('notes…')).toBeVisible();
});

test.describe('shots list on a touchscreen', () => {
  test.use({ hasTouch: true, viewport: { width: 1024, height: 600 } });

  test('taps a row to open details', async ({ page }) => {
    await withControlSocket(async (socket) => {
      await simulateShot(socket);
    });

    await gotoApp(page);
    await dismissPicker(page);
    await page.getByRole('button', { name: 'Shots' }).click();

    await expect(page.locator('.shots-panel__validation')).toHaveCount(0);
    await page.locator('.shots-panel__row-main').first().tap();
    await expect(page.locator('.shots-panel__validation')).toBeVisible();
  });
});

test('display route shows latest shot and recent shots from mock backend session', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await setClub(socket, 'driver');
    await simulateShot(socket);
    await setClub(socket, '7-iron');
    await simulateShot(socket);
    await setClub(socket, 'pw');
    await simulateShot(socket);
  });

  await gotoApp(page, '/display');

  await expect(page.getByText('OpenFlight Display')).toBeVisible();
  await expect(page.getByText('Socket connected')).toBeVisible();
  await expect(page.getByLabel('Recent shots').locator('.display-shot-chip')).toHaveCount(3);
  await expect(page.getByLabel('Recent shots')).toContainText('pw');
  await expect(page.getByLabel('Recent shots')).toContainText('7-iron');
});

test('unit toggle in the menu sheet updates displayed units', async ({ page }) => {
  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });

  await gotoApp(page);
  await dismissPicker(page);

  await expect(page.locator('.metric-card--selected .metric-card__unit')).toHaveText('mph');
  await expect(page.locator('.metric-card').filter({ hasText: 'Carry' }).locator('.metric-card__unit')).toHaveText(
    'yds'
  );

  const imperialSpeed = await page.locator('.metric-card--selected .metric-card__value').textContent();

  await openMenu(page);
  await page.getByRole('button', { name: 'KMH / M' }).click();
  await page.getByRole('button', { name: 'Close menu' }).click();

  await expect(page.locator('.metric-card--selected .metric-card__unit')).toHaveText('km/h');
  await expect(page.locator('.metric-card').filter({ hasText: 'Carry' }).locator('.metric-card__unit')).toHaveText('m');
  await expect(page.locator('.metric-card--selected .metric-card__value')).not.toHaveText(imperialSpeed ?? '');
});
