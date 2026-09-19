// @ts-check
/**
 * Bridges the new config loader (`getConfig('search-sources')`) to the
 * legacy `SCRAPER_CONFIG` global that the boot blocks of paper_database.html
 * and paper_scraper.html - plus every extracted scraper module — still read
 * from. Closes plan #9's last bullet ("all sources / categories / presets
 * reads come from getConfig").
 *
 * Why a bridge instead of a wholesale rewrite: the legacy callers expect
 *   - `cfg.sources` as a map keyed by source id (e.g. `cfg.sources.prl`)
 *   - `cfg.presets` as an array
 *   - `cfg.arxivCategories` as a string array
 *   - `cfg.autoFetch` as an object
 * The new `search-sources.json` schema stores `sources` as an *array* of
 * `{id, label, ...}` so user_config can target individual entries by id
 * via `x-mergeKey`. The bridge converts array → map at the boundary.
 *
 * After this runs, `data/user_config/search-sources.json` overrides flow
 * through the standard loader path. The legacy boot-block helper
 * `_applySettingsToConfig()` (which copied DB-saved sources/presets onto
 * SCRAPER_CONFIG) is now redundant - Settings v2 writes user_config files
 * directly via POST /api/config/search-sources.
 */

import { initConfig, getConfig } from './config.js';

/**
 * Apply the merged search-sources config to globalThis.SCRAPER_CONFIG.
 * Pure function (sync) - call after initConfig() resolves.
 *
 * Exported for tests; production callers should use bootstrapSearchConfig().
 *
 * @param {Record<string, any>} target  - usually globalThis.SCRAPER_CONFIG
 * @param {Record<string, any>} merged  - value from getConfig('search-sources')
 */
export function applySearchConfig(target, merged) {
  if (!target || !merged) return;
  if (Array.isArray(merged.sources)) {
    const sourcesMap = {};
    for (const s of merged.sources) {
      if (!s || !s.id) continue;
      const entry = {
        label: s.label,
        color: s.color,
        enabled: s.enabled,
        type: s.type,
      };
      if (s.journalRef !== undefined) entry.journalRef = s.journalRef;
      if (s.journalName !== undefined) entry.journalName = s.journalName;
      if (s.issn !== undefined) entry.issn = s.issn;
      sourcesMap[s.id] = entry;
    }
    target.sources = sourcesMap;
  }
  if (Array.isArray(merged.presets)) target.presets = merged.presets;
  if (Array.isArray(merged.arxivCategories)) target.arxivCategories = merged.arxivCategories;
  if (merged.autoFetch && typeof merged.autoFetch === 'object') {
    target.autoFetch = { ...(target.autoFetch || {}), ...merged.autoFetch };
  }
}

/**
 * Initialize the config loader and bridge merged search-sources values onto
 * globalThis.SCRAPER_CONFIG. On failure, leaves SCRAPER_CONFIG untouched -  * the static defaults from scraper_config.js remain in effect.
 *
 * Idempotent at call-time (re-runs initConfig + re-applies, which just
 * redoes the fetch + write - fine for the single-user local app).
 *
 * @param {function[]} [onReady]  - callbacks fired after the bridge applies,
 *                                  useful for re-rendering UI that was drawn
 *                                  against the static defaults before the
 *                                  fetch resolved
 * @param {object}     [opts]     - forwarded to initConfig (loader options:
 *                                  fetch, defaultsBase, overridesBase,
 *                                  schemaBase). Tests inject these; production
 *                                  callers omit them and use the loader's
 *                                  default URL bases.
 * @returns {Promise<void>}
 */
export async function bootstrapSearchConfig(onReady = [], opts = {}) {
  try {
    await initConfig(opts);
    const merged = getConfig('search-sources');
    applySearchConfig(globalThis.SCRAPER_CONFIG, merged);
    for (const cb of onReady) {
      try { cb(); }
      catch (e) { console.warn('[search-config-bridge] onReady callback threw:', e); }
    }
  } catch (e) {
    console.warn('[search-config-bridge] bootstrap failed; static defaults remain:', e);
  }
}
