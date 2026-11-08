import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { copyToClipboard } from '../src/utils/clipboard.ts';
import { interviewerInviteUrl, isInsecureInviteUrl, mediaUnavailableMessage } from '../src/utils/media.ts';

afterEach(() => { delete globalThis.navigator; delete globalThis.document; delete globalThis.window; });

test('interviewer URL goes directly to room and retains query parameters', () => {
  assert.equal(interviewerInviteUrl('https://example.com/invite/token?x=1'), 'https://example.com/invite/token/room?x=1&role=interviewer');
  assert.equal(isInsecureInviteUrl('http://192.168.1.1:5173/invite/t'), true);
  assert.equal(isInsecureInviteUrl('https://example.com/invite/t'), false);
});

test('reports insecure and unsupported media contexts', () => {
  globalThis.window = { isSecureContext: false };
  assert.match(mediaUnavailableMessage(), /HTTPS/);
  globalThis.window.isSecureContext = true;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {} });
  assert.match(mediaUnavailableMessage(), /不支持/);
});

test('copy falls back when clipboard is absent or rejects', async () => {
  let removed = false;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {} });
  globalThis.document = {
    createElement: () => ({ style: {}, setAttribute() {}, select() {}, remove() { removed = true; } }),
    body: { appendChild() {} }, execCommand: () => true,
  };
  assert.equal(await copyToClipboard('url'), true);
  assert.equal(removed, true);
  navigator.clipboard = { writeText: async () => { throw Error('denied'); } };
  document.execCommand = () => false;
  assert.equal(await copyToClipboard('url'), false);
  navigator.clipboard.writeText = async () => {};
  assert.equal(await copyToClipboard('url'), true);
});
