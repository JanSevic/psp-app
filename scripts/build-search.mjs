// Sestaví fulltextový index Pagefind z state/work/search-*.jsonl.gz do dist/pagefind.
// Každý záznam = vše, co jedna osoba řekla k jednomu bodu jedné schůze.
import * as pagefind from "pagefind";
import { createReadStream, readdirSync, existsSync } from "node:fs";
import { createGunzip } from "node:zlib";
import { createInterface } from "node:readline";
import path from "node:path";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const work = process.env.PSP_STATE_DIR ? path.join(process.env.PSP_STATE_DIR, "work") : path.join(root, "state", "work");
const dist = process.env.PSP_DIST_DIR || path.join(root, "dist");

const files = existsSync(work) ? readdirSync(work).filter((f) => /^search-\d+\.jsonl\.gz$/.test(f)).sort() : [];
if (!files.length) {
  console.log("Žádné záznamy pro fulltext – index se nevytváří.");
  process.exit(0);
}

const { index, errors } = await pagefind.createIndex({ forceLanguage: "cs" });
if (errors?.length) throw new Error(errors.join("\n"));

let n = 0;
for (const f of files) {
  const rl = createInterface({ input: createReadStream(path.join(work, f)).pipe(createGunzip()), crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    const r = JSON.parse(line);
    const res = await index.addCustomRecord({
      url: r.url,
      content: r.content,
      language: "cs",
      meta: { title: r.title, datum: r.datum || "", name: r.name },
      filters: { osoba: [r.osoba], obdobi: [r.obdobi] },
      sort: { datum: r.datum || "0000-00-00" },
    });
    if (res.errors?.length) console.warn(res.errors);
    if (++n % 20000 === 0) console.log(`  ${n} záznamů`);
  }
}
const out = await index.writeFiles({ outputPath: path.join(dist, "pagefind") });
if (out.errors?.length) throw new Error(out.errors.join("\n"));
console.log(`Fulltextový index: ${n} záznamů → ${out.outputPath}`);
await pagefind.close();
