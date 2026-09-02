/**
 * ════════════════════════════════════════════════════════════
 *  SCRAPER CONFIG — Edit this file to customize for your field
 * ════════════════════════════════════════════════════════════
 *
 *  This is the ONLY file you need to change to adapt the paper
 *  scraper + database for a different research area.
 *
 *  Both paper_scraper.html and paper_database.html load this
 *  file and use SCRAPER_CONFIG for all domain-specific behavior.
 *
 *  HOW TO CUSTOMIZE:
 *  1. Change `name` and `description` to match your field
 *  2. Replace `presets` with common searches in your area
 *  3. Replace `tags` with keyword→tag mappings for your domain
 *  4. Adjust `sources` to enable/disable journal feeds
 *  5. Optionally tweak `autoFetch` timing
 *
 *  Everything else (UI, database, search, citations) just works.
 * ════════════════════════════════════════════════════════════
 */

// `var` (not `const`) so the binding lands on `window` / `globalThis`. This
// file is a classic <script>; ES2015 const at the top level of a classic
// script goes into the script's lexical environment, NOT onto the global
// object. The migrated ES modules under src/ui/database/ read this via
// `globalThis.SCRAPER_CONFIG`, so it must be reachable there. Switching to
// `var` is the smallest fix that keeps both legacy and module callers happy.
var SCRAPER_CONFIG = {

  // ── Identity ──────────────────────────────────────────────
  // Shown in page headers and the suggestions banner.

  name: "SCQ",
  description: "Superconducting Circuits & Qubits",

  // ── arXiv Categories ────────────────────────────────────────────
  // Used by search_recent.js for daily auto-fetch and by the
  // browser-based scraper for category-filtered searches.
  arxivCategories: [
    "quant-ph",            // quantum physics (primary)
    "cond-mat.supr-con",   // superconductivity
    "cond-mat.mtrl-sci",   // materials science
    "cond-mat.mes-hall",   // mesoscale / nanoscale physics
  ],


  // ── Sources ───────────────────────────────────────────────
  // Available paper sources. Each key becomes a toggle button.
  //
  //   key:        internal ID (used in saved queries, localStorage)
  //   label:      button text in the UI
  //   color:      CSS color variable or hex for badges/buttons
  //   enabled:    whether the toggle is ON by default
  //   type:       "arxiv" = direct arXiv API search
  //               "arxiv-jr" = arXiv search filtered by journal_ref
  //   journalRef: (for "arxiv-jr" only) the journal_ref filter string
  //   journalName: full journal name for citations
  //
  // To add a new journal, copy one of the arxiv-jr entries and
  // change the key, label, journalRef, and journalName.
  // As long as papers appear on arXiv, this will find them.

  sources: {
    arxiv: {
      label: "arXiv",
      color: "#58a6ff",
      enabled: true,
      type: "arxiv",
    },
    prl: {
      label: "PRL",
      color: "#bc8cff",
      enabled: false,
      type: "arxiv-jr",
      journalRef: "Phys.+Rev.+Lett.",
      journalName: "Phys. Rev. Lett.",
    },
    pra: {
      label: "PR Applied",
      color: "#3fb950",
      enabled: false,
      type: "arxiv-jr",
      journalRef: "Phys.+Rev.+Applied",
      journalName: "Phys. Rev. Applied",
    },
    prm: {
      label: "PR Materials",
      color: "#39d2c0",
      enabled: false,
      type: "arxiv-jr",
      journalRef: "Phys.+Rev.+Materials",
      journalName: "Phys. Rev. Materials",
    },

    // ── Physical Review Journals (Crossref search) ─────────
    // These search Crossref directly, so they find published
    // papers even when there's no arXiv preprint.
    //
    //   type: "crossref"    = keyword search via Crossref API
    //   issn:               = ISSN used to filter Crossref results
    //   journalName:        = full journal name for citations
    //
    prb: {
      label: "PRB",
      color: "#d19a66",
      enabled: false,
      type: "crossref",
      issn: "2469-9950",
      journalName: "Phys. Rev. B",
    },
    prx: {
      label: "PRX",
      color: "#e06c75",
      enabled: false,
      type: "crossref",
      issn: "2160-3308",
      journalName: "Phys. Rev. X",
    },
    prresearch: {
      label: "PR Research",
      color: "#c678dd",
      enabled: false,
      type: "crossref",
      issn: "2643-1564",
      journalName: "Phys. Rev. Research",
    },
    prxquantum: {
      label: "PRX Quantum",
      color: "#e5c07b",
      enabled: false,
      type: "crossref",
      issn: "2691-3399",
      journalName: "PRX Quantum",
    },

    // ── Other journals (uncomment to use) ────────────────
    //
    // nature: {
    //   label: "Nature",
    //   color: "#e06c75",
    //   enabled: false,
    //   type: "crossref",
    //   issn: "1476-4687",
    //   journalName: "Nature",
    // },
    // science: {
    //   label: "Science",
    //   color: "#e5c07b",
    //   enabled: false,
    //   type: "crossref",
    //   issn: "1095-9203",
    //   journalName: "Science",
    // },
  },


  // ── Preset Queries ────────────────────────────────────────
  // Quick-search buttons shown above search results.
  // Replace these with common searches for your field.

  presets: [
    { label: "SCQ materials",       query: "superconducting qubit material" },
    { label: "Ta resonators",       query: "tantalum superconducting resonator" },
    { label: "Transmon coherence",  query: "transmon qubit coherence" },
    { label: "JJ fabrication",      query: "Josephson junction fabrication" },
    { label: "TLS loss",            query: "two-level system loss microwave" },
    { label: "Surface oxides",      query: "surface oxide superconducting" },

    // Examples for other fields:
    // { label: "Perovskite solar",  query: "perovskite solar cell efficiency" },
    // { label: "Topological ins.",  query: "topological insulator surface states" },
    // { label: "MOF catalysis",    query: "metal organic framework catalysis" },
  ],


  // ── Auto-Tag Keywords ─────────────────────────────────────
  // Maps tag names to arrays of keywords. When a paper's title
  // or abstract contains any keyword, the tag is auto-suggested.
  //
  // Tips:
  //   - Include trailing spaces for short terms to avoid false
  //     matches (e.g., "Al " won't match "algorithm")
  //   - Case-insensitive matching is applied automatically
  //   - Start with 10-20 tags; add more as your database grows

  tags: {
    "tantalum":          ["tantalum", "Ta ", "Ta-based", "beta-Ta", "α-Ta", "β-Ta"],
    "aluminum":          ["aluminum", "aluminium", "Al ", "Al-based", "Al2O3"],
    "niobium":           ["niobium", "Nb ", "NbN", "NbTiN", "Nb-based"],
    "TLS":               ["two-level system", "TLS", "two level system", "tunneling defect"],
    "surface loss":      ["surface loss", "surface participation", "surface impedance", "interface loss"],
    "Josephson junction": ["Josephson junction", "JJ ", "tunnel junction", "junction fabrication"],
    "transmon":          ["transmon", "Xmon", "fixed-frequency qubit"],
    "resonator":         ["resonator", "cavity", "CPW", "coplanar waveguide", "microwave cavity"],
    "qubit":             ["qubit", "quantum bit", "superconducting qubit"],
    "kinetic inductance": ["kinetic inductance", "MKID", "kinetic inductor"],
    "quasiparticle":     ["quasiparticle", "quasi-particle", "QP poisoning", "nonequilibrium"],
    "oxide":             ["oxide", "oxidation", "native oxide", "surface oxide", "AlOx", "TaOx", "NbOx"],
    "sapphire":          ["sapphire", "Al2O3 substrate", "c-plane sapphire"],
    "silicon":           ["silicon", "Si substrate", "Si wafer", "SOI", "silicon-on-insulator"],
    "coherence":         ["coherence", "T1", "T2", "relaxation time", "dephasing", "decoherence"],
    "decoherence":       ["decoherence", "dephasing", "noise", "dissipation", "loss mechanism"],
    "microwave":         ["microwave", "GHz", "RF ", "millimeter-wave"],
    "cryogenic":         ["cryogenic", "millikelvin", "dilution refrigerator", "mK ", "cryostat"],
    "fabrication":       ["fabrication", "deposition", "sputtering", "evaporation", "etching", "lithography"],
    "quality factor":    ["quality factor", "Q factor", "internal Q", "Qi "],
  },


  // ── Entry Types ──────────────────────────────────────────
  // Categorize entries in the database. Each key is stored in the
  // `entry_type` column. The label is shown in the UI, color is
  // used for badges.  "preprint" is the default for new papers.
  //
  // To add a new type, just add a key here — it becomes available
  // in the type filter and the "Add Website" modal automatically.

  entryTypes: {
    preprint:  { label: "Preprint",   color: "#58a6ff" },
    published: { label: "Published",  color: "#bc8cff" },
    website:   { label: "Website",    color: "#f0883e" },
    release:   { label: "New Release",color: "#3fb950" },
    thesis:    { label: "Thesis",     color: "#e5c07b" },
    review:    { label: "Review",     color: "#39d2c0" },

    // Examples for other types:
    // talk:    { label: "Talk/Slides", color: "#d19a66" },
    // dataset: { label: "Dataset",     color: "#c678dd" },
  },


  // ── Auto-Fetch Settings ───────────────────────────────────
  // Controls the background fetch that runs when you open
  // paper_database.html.

  autoFetch: {
    enabled: true,             // set false to disable auto-fetch entirely
    cooldownHours: 4,          // minimum hours between auto-fetches
    maxResultsPerQuery: 25,    // arXiv results per query (max 100)
    delayBetweenQueries: 1500, // ms between queries (rate limiting)
  },


  // ── Citation Style ────────────────────────────────────────
  // Controls how auto-generated citations are formatted.
  // Currently: Physical Review / REVTeX style.
  // To change, edit the functions below.

  formatBibTeX: function (paper) {
    const shortAuth = (paper.shortAuthors || "Unknown").replace(/[^a-zA-Z]/g, "");
    const year = paper.year || new Date().getFullYear();
    const key = (shortAuth + year).toLowerCase();
    let bib = `@article{${key},\n  title = {${paper.title}},\n  author = {${paper.authors}},\n  year = {${year}},`;
    if (paper.journal) bib += `\n  journal = {${paper.journal}},`;
    if (paper.doi) bib += `\n  doi = {${paper.doi}},`;
    if (paper.source === "arxiv" || paper.arxivId) {
      const aid = paper.arxivId || paper.id;
      bib += `\n  eprint = {${aid}},\n  archivePrefix = {arXiv},`;
    }
    bib += `\n  url = {${paper.url || ""}}\n}`;
    return bib;
  },

  formatPlainText: function (paper) {
    const year = paper.year || new Date().getFullYear();
    let cite = `${paper.authors}, "${paper.title},"`;
    if (paper.journal) cite += ` ${paper.journal}`;
    if (paper.doi) cite += ` (${year}), doi:${paper.doi}`;
    else if (paper.source === "arxiv") cite += ` arXiv:${paper.id} (${year})`;
    cite += ".";
    return cite;
  },
};

// Explicit global export so module callers (and static analysis) see the
// same object the classic <script> declared above.
globalThis.SCRAPER_CONFIG = SCRAPER_CONFIG;
