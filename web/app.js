// PSP-app – statický prohlížeč hlasování a projevů poslanců.
// Bez závislostí; data se načítají z ./data, fulltext z ./pagefind.

const PSP = "https://www.psp.cz";
const app = document.getElementById("app");
const cache = new Map();
let META = null; // periods.json
const ui = {}; // stav filtrů podle stránky

// ---------------------------------------------------------------- utility

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const norm = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const fmtDate = (d) => {
  if (!d) return "";
  const [y, m, dd] = d.split("-");
  return `${+dd}. ${+m}. ${y}`;
};
const fmtNum = (n) => (n == null ? "–" : Number(n).toLocaleString("cs-CZ"));
const codeClass = (c) => "v-" + (c === "@" ? "at" : c === "?" ? "q" : c);

async function getJSON(url) {
  if (cache.has(url)) return cache.get(url);
  const p = (async () => {
    const res = await fetch(url);
    if (!res.ok) {
      if (res.status === 404) return null;
      throw new Error(`${url}: HTTP ${res.status}`);
    }
    const buf = new Uint8Array(await res.arrayBuffer());
    let text;
    if (buf[0] === 0x1f && buf[1] === 0x8b) {
      const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
      text = await new Response(stream).text();
    } else {
      text = new TextDecoder().decode(buf);
    }
    return JSON.parse(text);
  })();
  cache.set(url, p);
  p.catch(() => cache.delete(url));
  return p;
}

const periodInfo = (num) => META.periods.find((p) => p.num === +num);
const yearOf = (d) => (d ? d.slice(0, 4) : "");

// Předložka před řadovou číslovkou období: „ve“ jen před souhláskovou skupinou
// (ve druhém, ve třetím, ve čtvrtém), jinak „v“ (v prvním, v pátém … v osmém, v devátém, v desátém).
const PREP_VE = new Set([2, 3, 4]);
const prep = (n) => (PREP_VE.has(+n) ? "ve" : "v");

// „v 9. období“, „ve 3. a 9. období“ – odkazy na stránku osoby v jiných obdobích
function periodsPhrase(nums, id) {
  const links = nums.map((n) => `<a href="#/${n}/o/${id}">${n}.</a>`);
  const list = links.length > 1 ? `${links.slice(0, -1).join(", ")} a ${links.at(-1)}` : links[0];
  return `${prep(nums[0])} ${list} období`;
}
const voteLabel = (c) => META.vote_codes[c] || (c === "?" ? "neznámo" : c);

function setTitle(t) {
  document.title = t ? `${t} – Poslanci` : "Poslanci – hlasování a projevy";
}

// ---------------------------------------------------------------- router

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, qs] = h.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { parts, q: new URLSearchParams(qs || "") };
}

async function route() {
  try {
    if (!META) {
      META = await getJSON("data/periods.json");
      if (!META) {
        app.innerHTML = `<p class="note">Data zatím nejsou k dispozici – noční zpracování ještě neproběhlo.</p>`;
        return;
      }
      document.getElementById("generated").textContent = META.generated || "–";
    }
    const { parts, q } = parseHash();
    if (!parts.length) {
      location.replace(`#/${META.periods[0].num}`);
      return;
    }
    const num = +parts[0];
    if (!periodInfo(num)) {
      app.innerHTML = `<p class="note">Neznámé volební období.</p>`;
      return;
    }
    renderPeriodNav(num);
    if (parts[1] === "o" && parts[2]) {
      await renderPerson(num, +parts[2], parts[3], parts[4]);
    } else {
      await renderPeriod(num, parts[1] || "poslanci", q);
    }
  } catch (err) {
    console.error(err);
    app.innerHTML = `<p class="note">Chyba při načítání: ${esc(err.message)}</p>`;
  }
}

window.addEventListener("hashchange", route);
route();

function renderPeriodNav(num) {
  const nav = document.getElementById("period-nav");
  nav.innerHTML = `<label>Volební období
    <select id="period-sel">${META.periods
      .map(
        (p) =>
          `<option value="${p.num}" ${p.num === num ? "selected" : ""}>${p.num}. (${p.year}–${yearOf(p.do)})</option>`
      )
      .join("")}</select></label>`;
  nav.querySelector("select").onchange = (e) => (location.hash = `#/${e.target.value}`);
}

// ---------------------------------------------------------------- období

async function renderPeriod(num, tab, q) {
  const info = periodInfo(num);
  const people = (await getJSON(`data/${num}/people.json`)) || [];
  setTitle(info.label);
  const tabs = [
    ["poslanci", "Poslanci"],
    ["ostatni", "Ostatní řečníci"],
    ["hledat", "Hledat v projevech"],
  ];
  const head = `<h1>${info.num}. volební období</h1>
    <p class="meta">${info.od ? `${info.do ? "" : "od "}${fmtDate(info.od)}${info.do ? ` – ${fmtDate(info.do)}` : ""} · ` : ""}${fmtNum(
      info.n_deputies
    )} poslanců · ${fmtNum(info.n_votes)} hlasování${info.has_speeches ? ` · ${fmtNum(info.n_speeches)} vystoupení` : ""}</p>
    <nav class="tabs">${tabs
      .map(([k, l]) => `<a href="#/${num}/${k}" class="${k === tab ? "on" : ""}">${l}</a>`)
      .join("")}</nav>`;

  if (tab === "hledat") {
    app.innerHTML = head + `<div id="search"></div>`;
    renderSearch(app.querySelector("#search"), { num, global: true, initial: q.get("q") || "" });
    return;
  }
  const deputies = tab === "poslanci";
  const list = people.filter((p) => p.poslanec === deputies);
  const key = `period-${num}-${tab}`;
  const st = (ui[key] ||= { text: "", klub: "", sort: "sort", dir: 1 });
  const clubs = [...new Set(list.map((p) => p.klub).filter(Boolean))].sort((a, b) => a.localeCompare(b, "cs"));

  app.innerHTML =
    head +
    (!deputies && !info.has_speeches
      ? `<p class="note">Projevy jsou zatím zpracované od volebního období ${META.speeches_from_year}.</p>`
      : "") +
    `<div class="filters">
      <label class="grow">Jméno<input id="f-text" type="search" value="${esc(st.text)}" placeholder="např. Novák"></label>
      ${
        deputies
          ? `<label>Klub<select id="f-klub"><option value="">všechny kluby</option>${clubs
              .map((c) => `<option ${c === st.klub ? "selected" : ""}>${esc(c)}</option>`)
              .join("")}</select></label>`
          : ""
      }
    </div>
    <p class="meta count" id="f-count"></p>
    <div class="table-wrap"><table><thead><tr>
      <th class="sort" data-k="sort">Jméno</th>
      ${deputies ? `<th class="sort" data-k="klub">Klub</th><th class="sort hide-sm" data-k="kraj">Kraj</th><th class="sort num" data-k="ucast">Účast</th>` : `<th class="sort" data-k="funkce">Funkce</th>`}
      ${info.has_speeches ? `<th class="sort num" data-k="vystoupeni">Vystoupení</th>` : ""}
    </tr></thead><tbody id="rows"></tbody></table></div>`;

  const labels = {};
  app.querySelectorAll("th.sort").forEach((th) => (labels[th.dataset.k] = th.textContent));
  const draw = () => {
    const t = norm(st.text);
    let rows = list.filter((p) => (!t || norm(p.name).includes(t)) && (!st.klub || p.klub === st.klub));
    rows.sort((a, b) => {
      const x = a[st.sort], y = b[st.sort];
      if (typeof x === "number" || typeof y === "number") return ((x ?? -1) - (y ?? -1)) * st.dir;
      return String(x ?? "").localeCompare(String(y ?? ""), "cs") * st.dir;
    });
    app.querySelectorAll("th.sort").forEach((th) => {
      const on = th.dataset.k === st.sort;
      th.innerHTML = esc(labels[th.dataset.k]) + (on ? `<span class="arrow" aria-hidden="true"> ${st.dir > 0 ? "↑" : "↓"}</span>` : "");
      th.setAttribute("aria-sort", on ? (st.dir > 0 ? "ascending" : "descending") : "none");
    });
    app.querySelector("#f-count").textContent = `${fmtNum(rows.length)} osob`;
    app.querySelector("#rows").innerHTML = rows
      .map(
        (p) => `<tr><td><a class="name" href="#/${num}/o/${p.id}">${esc(p.name)}</a></td>
        ${
          deputies
            ? `<td>${esc(p.klub || "")}</td><td class="hide-sm">${esc(p.kraj || "")}</td><td class="num">${
                p.ucast == null ? "–" : p.ucast.toLocaleString("cs-CZ") + " %"
              }</td>`
            : `<td>${esc(p.funkce || "")}</td>`
        }
        ${info.has_speeches ? `<td class="num">${fmtNum(p.vystoupeni)}</td>` : ""}</tr>`
      )
      .join("");
  };
  app.querySelector("#f-text").oninput = (e) => ((st.text = e.target.value), draw());
  const fk = app.querySelector("#f-klub");
  if (fk) fk.onchange = (e) => ((st.klub = e.target.value), draw());
  app.querySelectorAll("th.sort").forEach(
    (th) =>
      (th.onclick = () => {
        const k = th.dataset.k;
        st.dir = st.sort === k ? -st.dir : ["ucast", "vystoupeni"].includes(k) ? -1 : 1;
        st.sort = k;
        draw();
      })
  );
  draw();
}

// ---------------------------------------------------------------- osoba

async function renderPerson(num, id, tab, key) {
  const info = periodInfo(num);
  const d = await getJSON(`data/${num}/o/${id}.json`);
  if (!d) {
    app.innerHTML = `<p class="note">Osoba v tomto období nemá žádná data.</p>`;
    return;
  }
  setTitle(d.name);
  const tabs = [];
  if (d.poslanec) tabs.push(["hlasovani", "Hlasování"]);
  tabs.push(["projevy", "Projevy"], ["shrnuti", "Shrnutí"]);
  tabs.push(["statistiky", "Statistiky"]);
  if (info.has_speeches) tabs.push(["hledat", "Hledat"]);
  if (!tab || !tabs.some(([k]) => k === tab)) tab = tabs[0][0];

  const club = d.kluby?.length ? d.kluby[d.kluby.length - 1] : null;
  const role = d.poslanec ? (d.pohlavi === "M" ? "Poslanec" : "Poslankyně") : d.funkce || "Řečník";
  const fromTo = (a, b) => `${fmtDate(a)} – ${b ? fmtDate(b) : "dosud"}`;
  const facts = [];
  if (club)
    facts.push([
      "Klub",
      esc(club.nazev || club.zkratka) +
        (d.kluby.length > 1
          ? d.kluby.map((k) => `<span class="hist">${esc(k.zkratka)}: ${fromTo(k.od, k.do)}</span>`).join("")
          : ""),
    ]);
  if (d.kraj) facts.push(["Volební kraj", esc(d.kraj)]);
  if (d.kandidatka) facts.push([d.pohlavi === "M" ? "Zvolen za" : "Zvolena za", esc(d.kandidatka)]);
  if (d.mandat) facts.push(["Mandát", d.mandat.do ? fromTo(d.mandat.od, d.mandat.do) : `od ${fmtDate(d.mandat.od)}`]);
  const h = d.statistiky.hlasovani;
  const pr = d.statistiky.projevy;
  const nums = [];
  if (h?.celkem) nums.push(["Účast", h.ucast == null ? "–" : h.ucast.toLocaleString("cs-CZ") + " %"], ["Hlasování", fmtNum(h.celkem)]);
  if (info.has_speeches) nums.push(["Vystoupení", fmtNum(pr.vystoupeni)]);
  if (info.has_speeches && !h?.celkem) nums.push(["Body", fmtNum(pr.body)]);
  const numsHTML = nums.map(([a, b]) => `<div><div class="label">${a}</div><div class="n">${b}</div></div>`).join("");
  const back = `<a class="back" href="#/${num}">← ${info.num}. volební období</a>`;
  const profile = `<a href="${esc(d.psp_url)}" target="_blank" rel="noopener">Profil na psp.cz ↗</a>`;
  const name = esc(d.full_name || d.name);

  // široké okno: sloupec vlevo
  const side = `<aside class="person-side">${back}
    <section class="panel">
      <h1>${name}</h1>
      <p class="role">${esc(role)}</p>
      <dl class="facts">${facts.map(([a, b]) => `<div><dt>${a}</dt><dd>${b}</dd></div>`).join("")}</dl>
      ${nums.length ? `<div class="nums">${numsHTML}</div>` : ""}
      <p class="plinks">${profile}<span class="other-periods"></span></p>
    </section></aside>`;
  // úzké okno: hlavička přes celou šířku (dva nadpisy v DOM, viditelný je vždy jen jeden)
  const top = `<div class="person-top">${back}
    <section class="panel">
      <div class="who">
        <h1>${name}</h1>
        <p class="role">${esc(role)} · ${profile}<span class="other-periods"></span></p>
        <dl class="facts-row">${facts
          .map(([a, b]) => `<div><dt class="label">${a}</dt><dd>${a === "Klub" ? esc(club.zkratka) : b}</dd></div>`)
          .join("")}</dl>
      </div>
      ${nums.length ? `<div class="nums">${numsHTML}</div>` : ""}
    </section></div>`;

  app.innerHTML = `<div class="person">${side}
    <div class="person-main">${top}
      <nav class="tabs" aria-label="Záložky">${tabs
        .map(([k, l]) => `<a href="#/${num}/o/${id}/${k}" class="${k === tab ? "on" : ""}"${k === tab ? ' aria-current="page"' : ""}>${l}</a>`)
        .join("")}</nav>
      <div id="tab"></div>
    </div></div>`;
  otherPeriods(num, id);
  const el = app.querySelector("#tab");
  if (tab === "hlasovani") await renderVotes(el, num, d);
  else if (tab === "projevy") await renderSpeeches(el, num, d, key);
  else if (tab === "shrnuti") await renderSummaries(el, num, d);
  else if (tab === "statistiky") renderStats(el, d, info);
  else if (tab === "hledat") renderSearch(el, { num, osoba: id, name: d.name, pohlavi: d.pohlavi });
}

async function otherPeriods(num, id) {
  const found = [];
  for (const p of META.periods) {
    if (p.num === num) continue;
    const people = await getJSON(`data/${p.num}/people.json`).catch(() => null);
    if (people?.some((x) => x.id === id)) found.push(p.num);
  }
  if (!found.length) return;
  found.sort((a, b) => a - b);
  document.querySelectorAll(".other-periods").forEach((span) => {
    span.innerHTML = (span.closest(".person-top") ? " · také " : "<br>Také ") + periodsPhrase(found, id);
  });
}

function noSpeechesNote(info) {
  return `<p class="note">Projevy jsou zatím zpracované od volebního období ${META.speeches_from_year}. Pro starší období je najdete ve <a href="${PSP}/eknih/${info.year}ps/stenprot/index.htm" target="_blank" rel="noopener">stenoprotokolech na psp.cz</a>.</p>`;
}

// ---------------------------------------------------------------- hlasování

async function renderVotes(el, num, d) {
  const votes = await getJSON(`data/${num}/votes.json`);
  const C = Object.fromEntries(votes.cols.map((c, i) => [c, i]));
  const { offset, kody } = d.hlasovani;
  const rows = [];
  for (let i = 0; i < kody.length; i++) {
    const code = kody[i];
    if (code === ".") continue;
    rows.push({ v: votes.rows[offset + i], code });
  }
  rows.reverse(); // nejnovější nahoře
  if (!rows.length) {
    el.innerHTML = `<p class="note">K této osobě nejsou v tomto období žádná hlasování.</p>`;
    return;
  }
  const key = `votes-${num}-${d.id}`;
  const st = (ui[key] ||= { od: "", do: "", schuze: "", codes: new Set(), text: "", hideZm: false, limit: 200 });
  const codesPresent = [...new Set(rows.map((r) => r.code))];
  const schuzeList = [...new Set(rows.map((r) => r.v[C.schuze]))].sort((a, b) => b - a);

  el.innerHTML = `<div class="filters">
      <label>Od<input type="date" id="f-od" value="${st.od}"></label>
      <label>Do<input type="date" id="f-do" value="${st.do}"></label>
      <label>Schůze<select id="f-sch"><option value="">všechny</option>${schuzeList
        .map((s) => `<option value="${s}" ${String(s) === st.schuze ? "selected" : ""}>${s}.</option>`)
        .join("")}</select></label>
      <label class="grow">Hledat v názvu<input type="search" id="f-text" value="${esc(st.text)}"></label>
    </div>
    <div class="chips" role="group" aria-label="Jak hlasoval">${codesPresent
      .map(
        (c) =>
          `<button type="button" class="chip" data-code="${esc(c)}" aria-pressed="${st.codes.has(c)}">${esc(voteLabel(c))}</button>`
      )
      .join("")}
      <label class="check"><input type="checkbox" id="f-zm" ${st.hideZm ? "checked" : ""}> skrýt zmatečná</label>
    </div>
    <p class="meta count" id="count"></p>
    <div class="table-wrap"><table><thead><tr><th>Datum · schůze</th><th>Hlasování · výsledek</th><th>Hlasoval${
      d.pohlavi === "M" ? "" : "a"
    }</th></tr></thead>
    <tbody id="rows"></tbody></table></div>
    <button class="btn more" id="more">Zobrazit dalších 200</button>`;

  const draw = () => {
    const t = norm(st.text);
    const filtered = rows.filter(({ v, code }) => {
      const datum = v[C.datum] || "";
      if (st.od && datum < st.od) return false;
      if (st.do && datum > st.do) return false;
      if (st.schuze && String(v[C.schuze]) !== st.schuze) return false;
      if (st.codes.size && !st.codes.has(code)) return false;
      if (st.hideZm && v[C.zmatecne]) return false;
      if (t && !norm(v[C.nazev]).includes(t)) return false;
      return true;
    });
    el.querySelector("#count").textContent = `${fmtNum(filtered.length)} z ${fmtNum(rows.length)} hlasování · nejnovější nahoře`;
    el.querySelector("#rows").innerHTML = filtered
      .slice(0, st.limit)
      .map(({ v, code }) => {
        const zm = !!v[C.zmatecne];
        const res = zm ? "Z" : v[C.vysledek];
        const resLabel = res === "A" ? "přijato" : res === "R" ? "zamítnuto" : "zmatečné";
        return `<tr${zm ? ' class="zm"' : ""}>
          <td class="date"><span class="d">${fmtDate(v[C.datum])}</span>
            <div class="sub">${v[C.cas] ? esc(v[C.cas]) + " · " : ""}${v[C.schuze]}. schůze, č. ${v[C.cislo]}</div></td>
          <td><a href="${PSP}/sqw/hlasy.sqw?g=${v[C.id]}" target="_blank" rel="noopener">${esc(v[C.nazev] || "(bez názvu)")}</a>${
            zm ? '<span class="tag">zmatečné</span>' : ""
          }
            <div class="sub"><span class="res-${res}">${resLabel}</span>${
              zm ? "" : ` · <span class="num">${v[C.pro] ?? "–"} : ${v[C.proti] ?? "–"}</span>`
            }</div></td>
          <td><span class="vote ${codeClass(code)}">${esc(voteLabel(code))}</span></td>
        </tr>`;
      })
      .join("");
    el.querySelector("#more").style.display = filtered.length > st.limit ? "" : "none";
  };
  const reset = () => ((st.limit = 200), draw());
  el.querySelector("#f-od").onchange = (e) => ((st.od = e.target.value), reset());
  el.querySelector("#f-do").onchange = (e) => ((st.do = e.target.value), reset());
  el.querySelector("#f-sch").onchange = (e) => ((st.schuze = e.target.value), reset());
  el.querySelector("#f-text").oninput = (e) => ((st.text = e.target.value), reset());
  el.querySelector("#f-zm").onchange = (e) => ((st.hideZm = e.target.checked), reset());
  el.querySelectorAll(".chip").forEach(
    (b) =>
      (b.onclick = () => {
        const c = b.dataset.code;
        st.codes.has(c) ? st.codes.delete(c) : st.codes.add(c);
        b.setAttribute("aria-pressed", st.codes.has(c));
        reset();
      })
  );
  el.querySelector("#more").onclick = () => ((st.limit += 200), draw());
  draw();
}

// ---------------------------------------------------------------- projevy

async function renderSpeeches(el, num, d, focusKey) {
  const info = periodInfo(num);
  if (!info.has_speeches) {
    el.innerHTML = noSpeechesNote(info);
    return;
  }
  const groups = await getJSON(`data/${num}/s/${d.id}.json.gz`);
  if (!groups?.length) {
    el.innerHTML = `<p class="note">V tomto období nemá žádná zaznamenaná vystoupení.</p>`;
    return;
  }
  const key = `speeches-${num}-${d.id}`;
  const st = (ui[key] ||= { chair: true, limit: 25, text: "" });
  if (focusKey) {
    const idx = groups.findIndex((g) => g.key === focusKey);
    if (idx >= st.limit) st.limit = idx + 5;
  }
  el.innerHTML = `<div class="filters">
      <label class="grow">Bod nebo schůze<input type="search" id="f-text" value="${esc(st.text)}" placeholder="název bodu, číslo schůze"></label>
      <label class="check"><input type="checkbox" id="f-chair" ${st.chair ? "checked" : ""}> zobrazit i řízení schůze</label>
    </div><p class="meta count" id="count"></p><div id="list"></div><button class="btn more" id="more">Zobrazit další body</button>`;

  const draw = () => {
    const t = norm(st.text);
    const filtered = groups
      .map((g) => ({ ...g, speeches: st.chair ? g.speeches : g.speeches.filter((s) => s.role !== "chair") }))
      .filter((g) => (g.speeches.length || g.poznamky?.length) && (!t || norm(g.bod).includes(t) || `${g.schuze}` === t.replace(/\D/g, "")));
    el.querySelector("#count").textContent = `${fmtNum(filtered.length)} bodů`;
    el.querySelector("#list").innerHTML = filtered.slice(0, st.limit).map(groupHTML).join("");
    el.querySelector("#more").style.display = filtered.length > st.limit ? "" : "none";
    el.querySelectorAll("button.expand").forEach(
      (b) =>
        (b.onclick = () => {
          b.previousElementSibling.classList.remove("collapsed");
          b.remove();
        })
    );
  };
  el.querySelector("#f-text").oninput = (e) => ((st.text = e.target.value), (st.limit = 25), draw());
  el.querySelector("#f-chair").onchange = (e) => ((st.chair = e.target.checked), draw());
  el.querySelector("#more").onclick = () => ((st.limit += 25), draw());
  draw();
  if (focusKey) {
    const g = document.getElementById("g-" + focusKey);
    if (g) {
      g.classList.add("hl");
      g.scrollIntoView({ block: "start" });
    }
  }
}

function groupHTML(g) {
  return `<section class="group" id="g-${esc(g.key)}">
    <header><div class="label">${fmtDate(g.datum)} · ${g.schuze}. schůze${g.bod_cislo > 0 ? ` · bod ${g.bod_cislo}` : ""}</div>
    <h3>${esc(g.bod)}</h3></header>
    ${g.speeches.map(speechHTML).join("")}
    ${g.poznamky?.length ? notesHTML(g.poznamky) : ""}
  </section>`;
}

// Poznámky stenografů o osobě zapsané během vystoupení jiných řečníků (výkřiky, chování)
function notesHTML(notes) {
  return `<aside class="notes">
    <div class="title">Zaznamenáno ve stenozáznamu</div>
    <div class="meta">výkřiky a chování během vystoupení jiných řečníků</div>
    <ul>${notes
      .map(
        (n) => `<li><span class="t">(${esc(n.text)})</span><div class="meta">${n.odhad ? "přiřazeno podle kontextu · " : ""}během vystoupení: ${esc(n.kde)}${
          n.cas ? " · úsek od " + esc(n.cas) : ""
        } · <a href="${esc(n.url)}" target="_blank" rel="noopener">psp.cz ↗</a></div></li>`
      )
      .join("")}</ul>
  </aside>`;
}

function speechHTML(s) {
  const long = s.text.length > 6;
  return `<div class="speech ${s.role === "chair" ? "chair" : ""}">
    <div class="who"><strong>${esc(s.label)}</strong>
      <span class="meta">${s.datum ? fmtDate(s.datum) : ""}${s.cas ? ", úsek od " + esc(s.cas) : ""}</span>${
        s.role === "chair" ? '<span class="pill">řízení schůze</span>' : ""
      }</div>
    <div class="text ${long ? "collapsed" : ""}">${s.text.map((p) => `<p>${esc(p)}</p>`).join("")}</div>
    ${long ? `<button type="button" class="expand">Zobrazit celé vystoupení (${s.text.length - 3} dalších odstavců)</button>` : ""}
    <div class="links"><a href="${esc(s.url)}" target="_blank" rel="noopener">Kontext jednání na psp.cz ↗</a></div>
  </div>`;
}

// ---------------------------------------------------------------- shrnutí

async function renderSummaries(el, num, d) {
  const info = periodInfo(num);
  if (!info.has_speeches) {
    el.innerHTML = noSpeechesNote(info);
    return;
  }
  if (!d.body?.length) {
    el.innerHTML = `<p class="note">V tomto období nemá žádná zaznamenaná vystoupení.</p>`;
    return;
  }
  const sums = (await getJSON(`data/${num}/sum/${d.id}.json`)) || {};
  const key = `sums-${num}-${d.id}`;
  const st = (ui[key] ||= { hideChair: false, limit: 50, text: "" });
  el.innerHTML = `<div class="filters">
      <label class="grow">Bod nebo schůze<input type="search" id="f-text" value="${esc(st.text)}"></label>
      <label class="check"><input type="checkbox" id="f-chair" ${st.hideChair ? "checked" : ""}> skrýt body, kde jen ${
        d.pohlavi === "M" ? "řídil" : "řídila"
      } schůzi</label>
    </div>
    <p class="meta count"><span id="count"></span> · Shrnutí vytváří umělá inteligence z plného znění, citace v uvozovkách jsou doslovné.</p>
    <div id="list"></div><button class="btn more" id="more">Zobrazit další</button>`;
  const draw = () => {
    const t = norm(st.text);
    const list = d.body.filter((g) => {
      if (st.hideChair && !g.n_speaker && !g.n_pozn) return false;
      return !t || norm(g.bod).includes(t) || `${g.schuze}` === t.replace(/\D/g, "");
    });
    el.querySelector("#count").textContent = `${fmtNum(list.length)} bodů`;
    el.querySelector("#list").innerHTML = list
      .slice(0, st.limit)
      .map((g) => {
        const s = sums[g.key] || { s: "pending" };
        let body;
        if (s.s === "ok" || s.s === "short") {
          const tone = s.b.filter((b) => /^Tón:/.test(b));
          const rest = s.b.filter((b) => !/^Tón:/.test(b));
          body = `<ul>${rest.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>${tone.map((b) => `<p class="tone">${esc(b)}</p>`).join("")}`;
        } else if (s.s === "chair") body = `<p class="state">Pouze řízení schůze.</p>`;
        else body = `<p class="state">Shrnutí zatím není hotové – vytváří se postupně při nočním zpracování.</p>`;
        return `<section class="group summary"><header><div class="label">${fmtDate(g.datum)} · ${g.schuze}. schůze${
          g.bod_cislo > 0 ? ` · bod ${g.bod_cislo}` : ""
        } · ${g.n_speaker} vystoupení${g.n > g.n_speaker ? ` · ${g.n - g.n_speaker}× řízení` : ""}${
          g.n_pozn ? ` · ${g.n_pozn}× ve stenozáznamu` : ""
        }</div>
          <h3>${esc(g.bod)}</h3></header>
          ${body}
          <div class="links"><a href="#/${num}/o/${d.id}/projevy/${encodeURIComponent(g.key)}"><strong>Plné znění →</strong></a></div></section>`;
      })
      .join("");
    el.querySelector("#more").style.display = list.length > st.limit ? "" : "none";
  };
  el.querySelector("#f-text").oninput = (e) => ((st.text = e.target.value), (st.limit = 50), draw());
  el.querySelector("#f-chair").onchange = (e) => ((st.hideChair = e.target.checked), draw());
  el.querySelector("#more").onclick = () => ((st.limit += 50), draw());
  draw();
}

// ---------------------------------------------------------------- statistiky

function renderStats(el, d, info) {
  const h = d.statistiky.hlasovani;
  const p = d.statistiky.projevy;
  let html = "";
  if (h && h.celkem) {
    const order = ["A", "N", "C", "F", "K", "@", "M", "W"];
    const max = Math.max(...Object.values(h.pocty));
    html += `<h2>Hlasování</h2>
      <div class="cards">
        <div class="card"><div class="label">Účast</div><div class="n">${h.ucast == null ? "–" : h.ucast.toLocaleString("cs-CZ") + " %"}</div></div>
        <div class="card"><div class="label">Hlasování během mandátu</div><div class="n">${fmtNum(h.celkem)}</div></div>
      </div>
      <div class="table-wrap"><table><tbody>${order
        .filter((c) => h.pocty[c])
        .map(
          (c) => `<tr><td class="${codeClass(c)}">${esc(voteLabel(c))}</td><td class="num">${fmtNum(h.pocty[c])}</td>
          <td class="num">${((100 * h.pocty[c]) / h.celkem).toLocaleString("cs-CZ", { maximumFractionDigits: 1 })} %</td>
          <td style="width:45%"><div class="bar" style="width:${(100 * h.pocty[c]) / max}%"></div></td></tr>`
        )
        .join("")}</tbody></table></div>
      <p class="meta">Účast = podíl hlasování, kdy byl(a) přihlášen(a) (ano, ne, zdržel se, nehlasoval), ze všech hlasování během mandátu
      kromě hlasování před složením slibu.</p>`;
  }
  if (info.has_speeches) {
    html += `<h2>Projevy</h2><div class="cards">
      <div class="card"><div class="label">Vystoupení</div><div class="n">${fmtNum(p.vystoupeni)}</div></div>
      <div class="card"><div class="label">Řízení schůze (vstupy)</div><div class="n">${fmtNum(p.rizeni)}</div></div>
      <div class="card"><div class="label">Body, ke kterým ${d.pohlavi === "M" ? "mluvil" : "mluvila"}</div><div class="n">${fmtNum(p.body)}</div></div>
      <div class="card"><div class="label">Slov ve vystoupeních</div><div class="n">${fmtNum(p.slova)}</div></div>
    </div>`;
  }
  el.innerHTML = html || `<p class="note">Žádné statistiky.</p>`;
}

// ---------------------------------------------------------------- fulltext

let pagefindPromise = null;
function loadPagefind() {
  pagefindPromise ||= import(new URL("pagefind/pagefind.js", location.href).href).then(async (pf) => {
    await pf.options({ excerptLength: 40 });
    pf.init();
    return pf;
  });
  return pagefindPromise;
}

function renderSearch(el, { num, osoba, name, pohlavi, global, initial = "" }) {
  const st = (ui[`search-${num}-${osoba || "all"}`] ||= { q: initial, scope: "period" });
  el.innerHTML = `<div class="filters">
      <label class="grow">${osoba ? `Hledat v tom, co ${pohlavi === "M" ? "řekl" : "řekla"} ${esc(name)}` : "Hledat ve všech projevech"}
        <input type="search" id="q" value="${esc(st.q)}" placeholder="slovo nebo fráze"></label>
      ${
        global
          ? `<label>Rozsah<select id="scope"><option value="period" ${st.scope === "period" ? "selected" : ""}>toto období</option>
              <option value="all" ${st.scope === "all" ? "selected" : ""}>všechna období s projevy</option></select></label>`
          : ""
      }
    </div><p class="meta count" id="count"></p><ul class="results" id="res"></ul><button class="btn more" id="more" style="display:none">Další výsledky</button>`;
  let timer, results = [], shown = 0, seq = 0;
  const showMore = async (mySeq) => {
    const slice = results.slice(shown, shown + 20);
    shown += slice.length;
    const data = await Promise.all(slice.map((r) => r.data()));
    if (mySeq !== seq) return;
    el.querySelector("#res").insertAdjacentHTML(
      "beforeend",
      data
        .map((r) => {
          const url = "#" + (r.url.split("#")[1] || "");
          return `<li><a href="${esc(url)}"><strong>${esc(r.meta.title)}</strong></a>
            <div class="meta">${fmtDate(r.meta.datum)}</div><div>${r.excerpt}</div></li>`;
        })
        .join("")
    );
    el.querySelector("#more").style.display = shown < results.length ? "" : "none";
  };
  const run = async () => {
    const mySeq = ++seq;
    const q = st.q.trim();
    el.querySelector("#res").innerHTML = "";
    el.querySelector("#more").style.display = "none";
    if (!q) {
      el.querySelector("#count").textContent = "";
      return;
    }
    el.querySelector("#count").textContent = "Hledám…";
    let pf;
    try {
      pf = await loadPagefind();
    } catch {
      el.querySelector("#count").textContent = "Fulltextový index zatím není k dispozici.";
      return;
    }
    const filters = osoba ? { osoba: `${num}-${osoba}` } : st.scope === "period" ? { obdobi: String(num) } : {};
    const r = await pf.search(q, { filters });
    if (mySeq !== seq) return;
    results = r.results;
    shown = 0;
    el.querySelector("#count").textContent = `${fmtNum(results.length)} výsledků`;
    await showMore(mySeq);
  };
  el.querySelector("#q").oninput = (e) => {
    st.q = e.target.value;
    clearTimeout(timer);
    timer = setTimeout(run, 300);
  };
  const sc = el.querySelector("#scope");
  if (sc) sc.onchange = (e) => ((st.scope = e.target.value), run());
  el.querySelector("#more").onclick = () => showMore(seq);
  if (st.q) run();
}
