import { describe, it, expect, beforeEach } from 'vitest';
import bus from '../../core/events.js';
import {
  initConfig, getConfig, getErrors, reload, subscribe, _reset,
} from '../../core/config.js';
import { MANIFEST } from '../../config/loader.js';

const PERMISSIVE = { type: 'object' };

function makeFetch(map) {
  return async (url) => {
    const key = Object.keys(map).find((k) => url.endsWith(k));
    if (!key) return { ok: false, status: 404 };
    return { ok: true, status: 200, json: async () => map[key] };
  };
}

function buildMap(per) {
  const map = {};
  for (const d of MANIFEST) {
    map[`/defaults/${d}.json`] = per?.[d] ?? { domain: d };
    map[`/schema/${d}.schema.json`] = PERMISSIVE;
  }
  return map;
}

const baseOpts = (map) => ({
  fetch: makeFetch(map),
  defaultsBase: '/defaults/',
  overridesBase: '/overrides/',
  schemaBase: '/schema/',
});

describe('core/config', () => {
  beforeEach(() => { _reset(); bus.clear(); });

  it('getConfig before initConfig throws', () => {
    expect(() => getConfig('ui')).toThrow(/before initConfig/);
  });

  it('initConfig loads every domain', async () => {
    await initConfig(baseOpts(buildMap()));
    for (const d of MANIFEST) {
      expect(getConfig(d)).toEqual({ domain: d });
      expect(getErrors(d)).toEqual([]);
    }
  });

  it('reload re-fetches and notifies subscribers when value changes', async () => {
    const map = buildMap({ ui: { domain: 'ui', theme: 'light' } });
    await initConfig(baseOpts(map));
    let notified = null;
    subscribe('ui', (current, prev) => { notified = { current, prev }; });
    map['/defaults/ui.json'] = { domain: 'ui', theme: 'dark' };
    await reload('ui');
    expect(notified.current.theme).toBe('dark');
    expect(notified.prev.theme).toBe('light');
  });

  it('reload does NOT notify when value is unchanged', async () => {
    const map = buildMap();
    await initConfig(baseOpts(map));
    let calls = 0;
    subscribe('ui', () => calls++);
    await reload('ui');
    expect(calls).toBe(0);
  });

  it('emits config:<domain>:changed on the events bus', async () => {
    const map = buildMap({ ui: { domain: 'ui', theme: 'light' } });
    await initConfig(baseOpts(map));
    let received = null;
    bus.on('config:ui:changed', (p) => { received = p; });
    map['/defaults/ui.json'] = { domain: 'ui', theme: 'dark' };
    await reload('ui');
    expect(received.current.theme).toBe('dark');
    expect(received.previous.theme).toBe('light');
  });

  it('subscribe returns an unsubscribe function', async () => {
    const map = buildMap({ ui: { domain: 'ui', n: 0 } });
    await initConfig(baseOpts(map));
    let calls = 0;
    const off = subscribe('ui', () => calls++);
    map['/defaults/ui.json'] = { domain: 'ui', n: 1 };
    await reload('ui');
    off();
    map['/defaults/ui.json'] = { domain: 'ui', n: 2 };
    await reload('ui');
    expect(calls).toBe(1);
  });

  it('a throwing subscriber does not break siblings', async () => {
    const map = buildMap({ ui: { domain: 'ui', n: 0 } });
    await initConfig(baseOpts(map));
    const calls = [];
    subscribe('ui', () => { throw new Error('oops'); });
    subscribe('ui', () => calls.push('ok'));
    map['/defaults/ui.json'] = { domain: 'ui', n: 1 };
    await expect(reload('ui')).resolves.toBeTruthy();
    expect(calls).toEqual(['ok']);
  });
});
