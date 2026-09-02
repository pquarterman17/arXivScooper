#!/usr/bin/env node
/**
 * fetch_arxiv.js — Download arXiv paper metadata + PDF
 *
 * Usage:  node fetch_arxiv.js <arxiv_id>
 * Example: node fetch_arxiv.js 2603.17921
 *
 * Outputs:
 *   papers/<id>_<Author>_<ShortTitle>.pdf
 *   inbox/<id>_meta.json   (metadata for Claude to consume)
 *
 * Designed to run on the host machine via Desktop Commander,
 * since the Cowork sandbox cannot reach arxiv.org.
 * Works on Windows (via fetch.bat) and macOS/Linux (via fetch.sh).
 */

const https = require("https");
const http = require("http");
const fs = require("fs");
const path = require("path");

const BASE_DIR = path.resolve(__dirname, "..");
const PAPERS_DIR = path.join(BASE_DIR, "papers");
const INBOX_DIR = path.join(BASE_DIR, "inbox");

// --- Helpers ---

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https") ? https : http;
    client.get(url, { timeout: 30000 }, (res) => {
      // Follow redirects
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return httpsGet(res.headers.location).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
      }
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks)));
      res.on("error", reject);
    }).on("error", reject);
  });
}

function parseXmlTag(xml, tag) {
  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, "g");
  const matches = [];
  let m;
  while ((m = re.exec(xml))) matches.push(m[1].trim());
  return matches;
}

function parseAuthors(xml) {
  // Each <author><name>...</name></author>
  const authorBlocks = parseXmlTag(xml, "author");
  return authorBlocks.map((block) => {
    const names = parseXmlTag(block, "name");
    return names[0] || "Unknown";
  });
}

function parseCategories(xml) {
  const cats = [];
  const re = /<category[^>]*term="([^"]+)"/g;
  let m;
  while ((m = re.exec(xml))) cats.push(m[1]);
  return cats;
}

function sanitize(str) {
  return str.replace(/[^a-zA-Z0-9_]/g, "").substring(0, 40);
}

// --- Main ---

async function main() {
  const arxivId = process.argv[2];
  if (!arxivId) {
    console.error("Usage: node fetch_arxiv.js <arxiv_id>");
    process.exit(1);
  }

  fs.mkdirSync(PAPERS_DIR, { recursive: true });
  fs.mkdirSync(INBOX_DIR, { recursive: true });

  // 1. Fetch metadata from arXiv API
  console.log(`[1/3] Fetching metadata for ${arxivId}...`);
  const apiUrl = `https://arxiv.org/api/query?id_list=${arxivId}`;
  const xmlBuf = await httpsGet(apiUrl);
  const xml = xmlBuf.toString("utf8");

  // Extract the <entry> block
  const entries = parseXmlTag(xml, "entry");
  if (entries.length === 0) {
    console.error("ERROR: Paper not found on arXiv");
    process.exit(1);
  }
  const entry = entries[0];

  const title = parseXmlTag(entry, "title")[0].replace(/\s+/g, " ");
  const authors = parseAuthors(entry);
  const abstract = (parseXmlTag(entry, "summary")[0] || "").replace(/\s+/g, " ");
  const published = (parseXmlTag(entry, "published")[0] || "").substring(0, 10);
  const categories = parseCategories(entry);

  // Extract comment (e.g., "12 pages, 3 figures")
  const commentMatch = entry.match(/<arxiv:comment[^>]*>([\s\S]*?)<\/arxiv:comment>/);
  const comment = commentMatch ? commentMatch[1].trim() : "";

  console.log(`  Title:    ${title}`);
  console.log(`  Authors:  ${authors.join(", ")}`);
  console.log(`  Date:     ${published}`);
  console.log(`  Category: ${categories.join(", ")}`);
  if (comment) console.log(`  Comment:  ${comment}`);

  // 2. Download PDF
  const lastName = authors[0].split(" ").pop();
  const shortTitle = sanitize(title.split(/\s+/).slice(0, 4).join("_"));
  const pdfName = `${arxivId}_${lastName}_${shortTitle}.pdf`;
  const pdfPath = path.join(PAPERS_DIR, pdfName);

  // Single stat() call instead of exists()+stat() so there is no window
  // between the check and the use where the file can change underneath us.
  let existingSize = null;
  try {
    existingSize = fs.statSync(pdfPath).size;
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }

  if (existingSize !== null) {
    const sizeMB = (existingSize / 1048576).toFixed(1);
    console.log(`\n[2/3] PDF already exists: ${pdfName} (${sizeMB} MB)`);
  } else {
    console.log(`\n[2/3] Downloading PDF...`);
    const pdfUrl = `https://arxiv.org/pdf/${arxivId}`;
    const pdfBuf = await httpsGet(pdfUrl);
    fs.writeFileSync(pdfPath, pdfBuf);
    const sizeMB = (pdfBuf.length / 1048576).toFixed(1);
    console.log(`  Saved: ${pdfName} (${sizeMB} MB)`);
  }

  // 3. Save metadata JSON for Claude to consume
  const meta = {
    arxiv_id: arxivId,
    title,
    authors,
    abstract,
    published,
    categories,
    comment,
    pdf_file: pdfName,
    pdf_path: pdfPath,
  };

  const jsonPath = path.join(INBOX_DIR, `${arxivId}_meta.json`);
  fs.writeFileSync(jsonPath, JSON.stringify(meta, null, 2), "utf8");
  console.log(`\n[3/3] Metadata saved: inbox/${arxivId}_meta.json`);

  // Print JSON block for Claude to parse
  console.log("\n=== JSON ===");
  console.log(JSON.stringify(meta, null, 2));
  console.log("=== END ===");
  console.log("\nDONE");
}

main().catch((err) => {
  console.error("FATAL:", err.message);
  process.exit(1);
});
