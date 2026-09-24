import { test, type Page } from '@playwright/test';
import { expect, gotoApp } from './helpers';

/*
 * Kiosk Connections panel against the real Flask server started with
 * `--connectivity mock` (see playwright.config.ts). The mock accepts the
 * Wi-Fi password "openflight" and the Bluetooth passkey "123456".
 *
 * The mock's state lives for the whole server process, so these steps run in
 * order and each one leaves the state the next expects.
 */
test.describe.configure({ mode: 'serial' });

async function openConnectionsFromFooter(page: Page, name: RegExp) {
  await page.getByRole('button', { name: 'Close Select club' }).click();
  await page.locator('.panel-header').getByRole('button', { name }).click();
  const panel = page.getByRole('dialog', { name: 'Connections' });
  await expect(panel).toBeVisible();
  return panel;
}

async function typeOnScreen(page: Page, text: string) {
  const keyboard = page.getByRole('group', { name: 'Keyboard' });
  for (const char of text) {
    await keyboard.getByRole('button', { name: char, exact: true }).click();
  }
}

test('Wi-Fi status is reachable from the first-launch club picker', async ({ page }) => {
  await gotoApp(page);
  const picker = page.getByRole('dialog', { name: 'Select club' });
  await expect(picker).toBeVisible();
  await picker.getByRole('button', { name: /^Wi-Fi: OpenFlight Range/ }).click();
  const panel = page.getByRole('dialog', { name: 'Connections' });
  await expect(panel).toBeVisible();
  await expect(panel.getByText('Local network')).toBeVisible();
  await expect(panel.getByText('Internet', { exact: true })).toBeVisible();
  await expect(panel.getByText('OpenFlight Range')).toBeVisible();
  // No kiosk supervisor in this test server, so no Show desktop.
  await expect(panel.getByRole('tab', { name: 'Advanced' })).toHaveCount(0);
  await panel.getByRole('button', { name: 'Close connections' }).click();
  await expect(panel).toBeHidden();
  await expect(picker).toBeVisible();
});

test('joins a secured network with the on-screen keyboard, rejecting a wrong password', async ({ page }) => {
  await gotoApp(page);
  const panel = await openConnectionsFromFooter(page, /^Wi-Fi:/);

  await panel.getByRole('button', { name: /^Pro Shop, Secured/ }).click();
  const dialog = page.getByRole('dialog', { name: 'Password for Pro Shop' });
  await expect(dialog).toBeVisible();
  const connect = dialog.getByRole('button', { name: 'Connect' });
  await expect(connect).toBeDisabled();

  await typeOnScreen(page, 'wrongpass');
  await expect(dialog.getByRole('textbox')).toHaveAttribute('type', 'password');
  await connect.click();
  await expect(dialog).toBeHidden();
  await expect(panel.getByRole('alert')).toContainText('Wrong password for Pro Shop.');

  await panel.getByRole('button', { name: /^Pro Shop, Secured/ }).click();
  await typeOnScreen(page, 'openflight');
  await page.getByRole('dialog', { name: 'Password for Pro Shop' }).getByRole('button', { name: 'Show' }).click();
  await expect(page.getByRole('dialog', { name: 'Password for Pro Shop' }).getByRole('textbox')).toHaveValue(
    'openflight'
  );
  await page.getByRole('dialog', { name: 'Password for Pro Shop' }).getByRole('button', { name: 'Connect' }).click();
  await expect(panel.getByRole('status').filter({ hasText: 'Connected to Pro Shop' })).toBeVisible();
  await expect(panel.locator('.conn-row--current')).toContainText('Pro Shop');
  await expect(page.locator('.panel-header').getByRole('button', { name: /^Wi-Fi: Pro Shop/ })).toBeVisible();
});

test('shows local-only networks distinctly from the internet', async ({ page }) => {
  await gotoApp(page);
  const panel = await openConnectionsFromFooter(page, /^Wi-Fi:/);
  await panel.getByRole('button', { name: /^Clubhouse Guest, Open/ }).click();
  await expect(panel.getByRole('status').filter({ hasText: 'Connected to Clubhouse Guest' })).toBeVisible();
  await expect(panel.locator('.conn-summary')).toContainText('Sign-in required');
  await expect(panel.locator('.conn-summary')).toContainText('OpenFlight works without internet.');
  await expect(page.locator('.panel-header').getByRole('button', { name: /no internet$/ })).toBeVisible();
});

test('disconnects and forgets networks', async ({ page }) => {
  await gotoApp(page);
  const panel = await openConnectionsFromFooter(page, /^Wi-Fi:/);
  const current = panel.locator('.conn-row--current');
  await current.getByRole('button', { name: 'Disconnect' }).click();
  await expect(current).toContainText('Not connected to Wi-Fi');
  await expect(page.locator('.panel-header').getByRole('button', { name: 'Wi-Fi not connected' })).toBeVisible();

  const row = panel.locator('.conn-item').filter({ hasText: 'Pro Shop' });
  await expect(row).toContainText('Saved');
  await row.getByRole('button', { name: 'Forget' }).click();
  await expect(panel.getByRole('status').filter({ hasText: 'Forgot Pro Shop' })).toBeVisible();
  await expect(row).not.toContainText('Saved');
});

test('pairs Bluetooth devices with passkey confirmation and entry', async ({ page }) => {
  await gotoApp(page);
  const panel = await openConnectionsFromFooter(page, /^Bluetooth/);

  // Numeric comparison.
  await panel.locator('.conn-item').filter({ hasText: 'Garage Printer' }).getByRole('button', { name: 'Pair' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Pair with Garage Printer' });
  await expect(confirm).toContainText('123456');
  await confirm.getByRole('button', { name: 'Pair' }).click();
  await expect(confirm).toBeHidden();
  await expect(panel.getByRole('status').filter({ hasText: 'Paired with Garage Printer' })).toBeVisible();
  const printer = panel.locator('.conn-item').filter({ hasText: 'Garage Printer' });
  await expect(printer.getByRole('button', { name: 'Disconnect' })).toBeVisible();

  // Passkey entry on the digit keypad.
  await panel.locator('.conn-item').filter({ hasText: 'Range Speaker' }).getByRole('button', { name: 'Pair' }).click();
  const entry = page.getByRole('alertdialog', { name: 'Pair with Range Speaker' });
  const pair = entry.getByRole('button', { name: 'Pair' });
  await expect(pair).toBeDisabled();
  for (const digit of '123456') {
    await entry.getByRole('button', { name: digit, exact: true }).click();
  }
  await pair.click();
  await expect(panel.getByRole('status').filter({ hasText: 'Paired with Range Speaker' })).toBeVisible();

  // Disconnect and forget.
  await printer.getByRole('button', { name: 'Disconnect' }).click();
  await expect(printer.getByRole('button', { name: 'Connect' })).toBeVisible();
  await printer.getByRole('button', { name: 'Forget' }).click();
  await expect(printer.getByRole('button', { name: 'Pair' })).toBeVisible();
});

test('rejecting a pairing prompt reports it and leaves the device unpaired', async ({ page }) => {
  await gotoApp(page);
  const panel = await openConnectionsFromFooter(page, /^Bluetooth/);
  const printer = panel.locator('.conn-item').filter({ hasText: 'Garage Printer' });
  await printer.getByRole('button', { name: 'Pair' }).click();
  const prompt = page.getByRole('alertdialog', { name: 'Pair with Garage Printer' });
  await prompt.getByRole('button', { name: 'Cancel' }).click();
  await expect(panel.getByRole('alert')).toContainText('Cancelled.');
  await expect(printer.getByRole('button', { name: 'Pair' })).toBeVisible();
});

test('turns Bluetooth off and on', async ({ page }) => {
  await gotoApp(page);
  const panel = await openConnectionsFromFooter(page, /^Bluetooth/);
  await panel.getByRole('button', { name: 'Turn off' }).click();
  await expect(panel.getByText('Bluetooth is off.')).toBeVisible();
  await expect(page.locator('.panel-header').getByRole('button', { name: 'Bluetooth off' })).toBeVisible();
  await panel.getByRole('button', { name: 'Turn on' }).click();
  await expect(panel.getByRole('button', { name: 'Find devices' })).toBeVisible();
});
