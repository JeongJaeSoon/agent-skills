// Runs inside the page. ui_measure.py wraps it as `(OPTS) => {...}` and reads back the returned object.
// Every check returns {id, status, items}; an item names the element and the numbers it failed on.
(OPTS) => {
  const IGNORE = "[data-ui-review-ignore]";
  const scope = (OPTS.scope && document.querySelector(OPTS.scope)) || document.body;
  const excluded = OPTS.exclude.flatMap((s) => [...document.querySelectorAll(s)]);
  const vw = window.innerWidth, vh = window.innerHeight;
  const px = (v) => parseFloat(v) || 0;
  const r1 = (v) => Math.round(v * 10) / 10;
  const cs = (el) => getComputedStyle(el);

  const skipped = (el) => !!el.closest(IGNORE) || excluded.some((x) => x.contains(el));
  const visible = (el) => {
    if (skipped(el)) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    // checkVisibility also sees an ancestor's opacity: 0 (a closed modal or panel).
    return el.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  };
  // The scope element itself counts: --scope '#members' on a table still measures that table.
  const within = (sel) => [scope, ...scope.querySelectorAll(sel)].filter((el) => el.matches(sel));
  const all = within("*").filter((el) => !["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "HEAD", "META", "LINK"].includes(el.tagName) && visible(el));
  const kids = (el) => [...el.children].filter(visible);
  const box = (el) => el.getBoundingClientRect();
  const text = (el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  const name = (el) => {
    let tag = el.tagName.toLowerCase();
    if (el.id) tag += "#" + el.id;
    else if (el.getAttribute("aria-label")) tag += `[aria-label="${el.getAttribute("aria-label").slice(0, 30)}"]`;
    const t = text(el).slice(0, 32);
    return t ? `${tag} "${t}"` : tag;
  };
  const where = (el) => {
    const p = el.parentElement && el.parentElement !== scope ? el.parentElement.closest("section, form, table, ul, ol, nav, [role=region], [role=table], [role=list], h1, h2, main") : null;
    return p && p !== el ? `${name(el)} in ${p.tagName.toLowerCase()}${p.id ? "#" + p.id : ""}` : name(el);
  };
  // The value most members share, or null when no value reaches half of them.
  const majority = (vals, tol = 1) => {
    let best = null, n = 0;
    for (const v of vals) {
      const c = vals.filter((w) => Math.abs(w - v) <= tol).length;
      if (c > n) { best = v; n = c; }
    }
    return n * 2 >= vals.length && n < vals.length ? best : null;
  };
  // What is drawn inside el (text line boxes, replaced and control boxes), not its stretched box; firstBottom ends the first line.
  const ink = (el) => {
    const rects = [];
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walk.currentNode; n; n = walk.nextNode()) {
      if (n.nodeType === 3) {
        if (!n.textContent.trim() || !visible(n.parentElement)) continue;
        const rg = document.createRange();
        rg.selectNodeContents(n);
        rects.push(...rg.getClientRects());
      } else if (/^(IMG|SVG|svg|INPUT|SELECT|TEXTAREA|BUTTON|VIDEO|CANVAS)$/.test(n.tagName) && visible(n)) {
        rects.push(box(n));
      }
    }
    const rs = rects.filter((r) => r.width > 0 && r.height > 0);
    if (!rs.length) return null;
    const top = Math.min(...rs.map((r) => r.top));
    const first = rs.filter((r) => r.top - top < 2);
    return { top, bottom: Math.max(...rs.map((r) => r.bottom)), firstBottom: Math.max(...first.map((r) => r.bottom)), lines: new Set(rs.map((r) => Math.round(r.top))).size };
  };
  const BUTTON = "button, [role=button], input[type=button], input[type=submit], [role=switch], a.btn";
  const FIELD = "input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=button]):not([type=submit]), select, [role=combobox]";

  // Sets of repeated rows: table body rows, list items, ARIA rows, and 3+ siblings sharing tag and class.
  const groups = [];
  const seen = new Set();
  const addGroup = (rows, label) => {
    rows = rows.filter(visible);
    if (rows.length < 2) return;
    const key = rows.map((r) => all.indexOf(r)).join(",");
    if (seen.has(key)) return;
    seen.add(key);
    groups.push({ rows, label });
  };
  for (const t of within("table")) addGroup([...t.querySelectorAll(":scope > tbody > tr, :scope > tr")], "table rows");
  for (const l of within("ul, ol, [role=list]")) addGroup([...l.children], "list items");
  // A key-value list: each term and value should take one line, so its label or value wrapping shows here.
  for (const d of within("dl")) addGroup([...d.querySelectorAll(":scope > dt, :scope > dd, :scope > div > dt, :scope > div > dd")], "definition list");
  for (const g of within("[role=table], [role=grid], [role=rowgroup]")) addGroup([...g.querySelectorAll(":scope > [role=row]")], "rows");
  for (const p of all) {
    const by = {};
    for (const c of kids(p)) {
      if (!c.className || typeof c.className !== "string" || cs(c).display.startsWith("inline")) continue;
      (by[c.tagName + "|" + c.className] ||= []).push(c);
    }
    for (const rows of Object.values(by)) if (rows.length >= 3) addGroup(rows, "repeated siblings");
  }

  const checks = [];
  const check = (id, items, failing = "FAIL") => checks.push({ id, status: items.length ? failing : "PASS", items: items.slice(0, OPTS.maxItems) , count: items.length });

  // 1. Rows of one set have one height.
  const rowItems = [];
  // A row lays its parts out side by side; a label stacked over its field is a form group, not a row.
  const sideBySide = (r) => {
    if (r.tagName === "TR") return true;
    const bs = kids(r).map(box);
    return bs.some((a, i) => bs.some((b, j) => j > i && a.top < b.bottom - 1 && b.top < a.bottom - 1));
  };
  const textOnly = (r) => /^(block|list-item)$/.test(cs(r).display) && [...r.querySelectorAll("*")].every((e) => cs(e).display.startsWith("inline") && !e.matches(BUTTON + ", " + FIELD));
  for (const g of groups) {
    const hs = g.rows.map((r) => box(r).height);
    const m = majority(hs);
    // Panels (nav sections, cards) are not rows, and rows of plain text are running text that may wrap.
    if (m === null || m > 120) continue;
    if (g.label !== "definition list" && (g.rows.every(textOnly) || !g.rows.some(sideBySide))) continue;
    g.rows.forEach((r, i) => { if (Math.abs(hs[i] - m) > 1) rowItems.push({ el: where(r), detail: `${g.label}: ${r1(hs[i])}px, the others ${r1(m)}px` }); });
  }
  check("row-height", rowItems);

  // 2. Controls in the same column of a set have one width and one right edge.
  const colItems = [];
  for (const g of groups) {
    // Cards and panels repeat too, but their buttons are not one column.
    if (g.rows.some((r) => box(r).height > 96)) continue;
    const cols = {};
    for (const r of g.rows) {
      const bs = [...r.querySelectorAll(BUTTON + ", " + FIELD)].filter(visible);
      bs.reverse().forEach((b, i) => (cols[i] ||= []).push(b));
    }
    for (const [i, bs] of Object.entries(cols)) {
      if (bs.length < 2) continue;
      const ws = bs.map((b) => box(b).width), rs = bs.map((b) => box(b).right);
      const mw = majority(ws), mr = majority(rs, 2);
      bs.forEach((b, k) => {
        if (mw !== null && Math.abs(ws[k] - mw) > 1) colItems.push({ el: where(b), detail: `column ${+i + 1} from the right: width ${r1(ws[k])}px, the others ${r1(mw)}px` });
        else if (mr !== null && Math.abs(rs[k] - mr) > 2) colItems.push({ el: where(b), detail: `column ${+i + 1} from the right: right edge ${r1(rs[k])}px, the others ${r1(mr)}px` });
      });
    }
  }
  check("control-column", colItems);

  // 3. Stacked siblings share their left edge, and full-width ones their right edge: a near miss is a misalignment.
  const alignItems = [];
  const NEAR = OPTS.nearMiss;
  const blockish = (el) => /^(block|flex|grid|table|flow-root|list-item)$/.test(cs(el).display);
  for (const p of all) {
    const cs_ = kids(p).filter((c) => blockish(c) && cs(c).position !== "absolute" && cs(c).position !== "fixed");
    if (cs_.length < 2) continue;
    const bs = cs_.map(box);
    const stacked = bs.every((b, i) => i === 0 || b.top >= bs[i - 1].bottom - 1);
    if (!stacked) continue;
    const inner = box(p).width - px(cs(p).paddingLeft) - px(cs(p).paddingRight);
    for (let i = 1; i < bs.length; i++) {
      const dl = Math.abs(bs[i].left - bs[0].left);
      if (dl > 0.5 && dl <= NEAR) alignItems.push({ el: where(cs_[i]), detail: `left edge ${r1(bs[i].left)}px vs ${r1(bs[0].left)}px of ${name(cs_[0])}` });
      const wide = bs[i].width >= inner * 0.5 && bs[0].width >= inner * 0.5;
      const dr = Math.abs(bs[i].right - bs[0].right);
      if (wide && dr > 0.5 && dr <= NEAR) alignItems.push({ el: where(cs_[i]), detail: `right edge ${r1(bs[i].right)}px vs ${r1(bs[0].right)}px of ${name(cs_[0])}` });
    }
  }
  check("edge-alignment", alignItems);

  // 4. The things drawn in one row sit on one center line.
  const centerItems = [];
  const rowLike = all.filter((el) => {
    const s = cs(el);
    if (el.tagName === "TR") return true;
    return /flex$/.test(s.display) && s.flexDirection === "row" && box(el).height <= 96;
  });
  for (const row of rowLike) {
    const cells = kids(row);
    if (cells.length < 2) continue;
    const bs = cells.map(box);
    if (!bs.every((b) => b.top < bs[0].bottom && b.bottom > bs[0].top)) continue;
    const inks = cells.map(ink).filter(Boolean);
    if (inks.length < 2) continue;
    // A multi-line cell may be centered as a block or aligned to its first line; either passes.
    const spreadOf = (cs_) => Math.max(...cs_) - Math.min(...cs_);
    const whole = spreadOf(inks.map((k) => (k.top + k.bottom) / 2));
    const first = spreadOf(inks.map((k) => (k.top + k.firstBottom) / 2));
    const spread = Math.min(whole, first);
    if (spread > OPTS.centerTol) centerItems.push({ el: where(row), detail: `content centers spread ${r1(spread)}px across ${inks.length} cells` });
  }
  check("vertical-center", centerItems);

  // 5. Spacing sits on the grid. Hairlines (<= 2px) and centering margins are left alone.
  const offGrid = {};
  for (const el of all) {
    const s = cs(el);
    const sides = ["paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "rowGap", "columnGap", "marginTop", "marginBottom"];
    if (Math.abs(px(s.marginLeft) - px(s.marginRight)) > 0.5) sides.push("marginLeft", "marginRight");
    for (const k of sides) {
      const v = Math.abs(px(s[k]));
      // Above 64px a margin is almost always auto (pushing an item to the far side), not a spacing token.
      if (v <= 2 || (k.startsWith("margin") && v > 64) || OPTS.allow.includes(v)) continue;
      if (Math.abs(v / OPTS.grid - Math.round(v / OPTS.grid)) > 0.01) ((offGrid[v] ||= []).push(`${name(el)} ${k}`));
    }
  }
  check("spacing-grid", Object.entries(offGrid).map(([v, xs]) => ({ el: xs.slice(0, 3).join("; "), detail: `${v}px is off the ${OPTS.grid}px grid (${xs.length} uses)` })));

  // 6. Nothing is wider than the viewport.
  const overItems = [];
  const sw = document.documentElement.scrollWidth;
  // The document width speaks for the whole page only; with --scope or --exclude the elements below decide.
  if (sw > vw && scope === document.body && !excluded.length) overItems.push({ el: "document", detail: `scrollWidth ${sw}px > viewport ${vw}px` });
  const clipsX = (el) => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) if (/(auto|scroll|hidden|clip)/.test(cs(a).overflowX)) return true; return false; };
  for (const el of all) if (box(el).right > vw + 1 && cs(el).position !== "fixed" && !clipsX(el) && !(el.parentElement && box(el.parentElement).right > vw + 1)) overItems.push({ el: where(el), detail: `right edge ${r1(box(el).right)}px > viewport ${vw}px` });
  check("horizontal-overflow", overItems);

  // 7. Underline marks a link inside running text, and only there.
  const linkItems = [];
  for (const a of all.filter((el) => el.matches("a[href]") && !el.matches(BUTTON))) {
    const underlined = cs(a).textDecorationLine.includes("underline");
    const block = a.closest("p, blockquote, dd, figcaption, [data-prose]");
    const prose = block && text(block).length > text(a).length + 1;
    if (underlined && !prose) linkItems.push({ el: where(a), detail: "underlined link outside running text (list, table, nav or card)" });
    if (!underlined && prose && cs(a).color === cs(a.parentElement).color && cs(a).fontWeight === cs(a.parentElement).fontWeight) linkItems.push({ el: where(a), detail: "link in running text looks like the text around it" });
  }
  check("link-underline", linkItems);

  // 8. Header cells of every table on the page look alike.
  const heads = all.filter((el) => el.matches("th, [role=columnheader]"));
  const sig = (el) => { const s = cs(el); return `${s.fontSize} ${s.fontWeight} ${s.textTransform} ${s.letterSpacing} ${s.color} h ${Math.round(box(el).height)}`; };
  const headSigs = {};
  for (const h of heads) (headSigs[sig(h)] ||= []).push(h);
  const headList = Object.entries(headSigs);
  check("table-head", headList.length > 1 ? headList.map(([s, hs]) => ({ el: hs.slice(0, 2).map(where).join("; "), detail: `${hs.length} header cells styled ${s}` })) : []);

  // 9. Text inputs, selects and comboboxes share one height and corner radius.
  const fields = all.filter((el) => el.matches(FIELD));
  const fieldSig = (el) => { const s = cs(el); return `h ${Math.round(box(el).height)} radius ${s.borderTopLeftRadius} font ${s.fontSize}`; };
  const fieldSigs = {};
  for (const f of fields) (fieldSigs[fieldSig(f)] ||= []).push(f);
  const fieldList = Object.entries(fieldSigs);
  check("field-consistency", fieldList.length > 1 ? fieldList.map(([s, fs]) => ({ el: fs.slice(0, 2).map(where).join("; "), detail: `${fs.length} fields with ${s}` })) : []);

  // 10. A bare glyph is not a label or a status.
  const glyphItems = [];
  for (const el of all) {
    if (kids(el).some((c) => text(c))) continue;
    const t = text(el);
    if (!/^(…|\.\.\.|!+|\?+|!\?)$/.test(t)) continue;
    const named = el.closest(BUTTON + ", a")?.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("aria-label");
    if (!named) glyphItems.push({ el: where(el), detail: `"${t}" with no name or tooltip` });
  }
  // The same for an icon standing alone: a control holding only an icon (aria-hidden or not, the control
  // still needs a name), or a non-hidden icon whose parent has no words either.
  const named = (el) => ["aria-label", "aria-labelledby", "title"].some((k) => el.getAttribute(k)) || !!el.querySelector("svg title, img[alt]:not([alt=''])");
  const icons = (el) => [...el.querySelectorAll("svg, img")].some(visible);
  for (const ctl of all.filter((el) => el.matches(BUTTON + ", a[href]") && !text(el) && icons(el) && !named(el))) glyphItems.push({ el: where(ctl), detail: "icon with no name or tooltip" });
  for (const svg of all.filter((el) => el.tagName.toLowerCase() === "svg" && el.getAttribute("aria-hidden") !== "true" && !el.closest(BUTTON + ", a[href]"))) {
    const host = svg.parentElement;
    if (text(host) || text(host.parentElement || host) || named(svg) || named(host)) continue;
    glyphItems.push({ el: where(host), detail: "icon with no name or tooltip" });
  }
  check("bare-glyph", glyphItems);

  // 11. A single value (URL, path, address, token) stays on one line.
  const wrapItems = [];
  for (const el of all) {
    if (kids(el).length) continue;
    const t = text(el);
    if (!/^(https?:\/\/|wss?:\/\/|\/[\w.-]+\/|[\w.+-]+@[\w-]+\.)\S+$/.test(t)) continue;
    const k = ink(el);
    if (k && k.lines > 1) wrapItems.push({ el: where(el), detail: `value wraps onto ${k.lines} lines` });
  }
  check("value-wrap", wrapItems, "WARN");

  // 12. An action names its object.
  const generic = new Set(OPTS.genericLabels.map((s) => s.toLowerCase()));
  check("generic-action", all.filter((el) => el.matches(BUTTON + ", a[href]") && generic.has(text(el).toLowerCase())).map((el) => ({ el: where(el), detail: "the label does not say what it acts on" })), "WARN");

  const bodyBg = cs(document.body).backgroundColor;
  const filled = (el) => { const s = cs(el); return (s.backgroundColor !== bodyBg && !/rgba\(.*, 0\)$|transparent/.test(s.backgroundColor)) || px(s.borderTopWidth) + px(s.borderBottomWidth) > 0; };
  // An on/off setting looks like a switch (a track with a thumb), not a button labeled with its state,
  // whatever its role: the role fixes screen readers, not what people see.
  const onOff = new Set(OPTS.toggleLabels.map((s) => s.toLowerCase()));
  const drawn = (el) => [...el.querySelectorAll("*")].some((c) => visible(c) && !text(c) && (c.tagName.toLowerCase() === "svg" || filled(c)));
  check("toggle-as-button", all.filter((el) => el.matches("button, [role=button], [role=switch]") && onOff.has(text(el).toLowerCase()) && !drawn(el)).map((el) => ({ el: where(el), detail: "on/off state drawn as a text button; draw a switch" })));

  // 13. The first screen is not mostly empty: share of the viewport covered by text, controls, media and bordered or filled boxes.
  const rects = [];
  for (const el of all) {
    const b = box(el);
    if (b.top >= vh || b.bottom <= 0) continue;
    if (/^(IMG|svg|SVG|INPUT|SELECT|TEXTAREA|BUTTON|VIDEO|CANVAS)$/.test(el.tagName) || (filled(el) && b.width * b.height < vw * vh * 0.5)) rects.push(b);
    for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) { const rg = document.createRange(); rg.selectNodeContents(n); rects.push(...rg.getClientRects()); }
  }
  let hit = 0, total = 0;
  for (let y = 4; y < vh; y += 8) for (let x = 4; x < vw; x += 8) { total++; if (rects.some((r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom)) hit++; }
  const coverage = total ? hit / total : 1;
  check("sparse-screen", coverage < OPTS.sparse ? [{ el: "first screen", detail: `${Math.round(coverage * 100)}% of the viewport holds content (threshold ${Math.round(OPTS.sparse * 100)}%)` }] : [], "WARN");

  // 14. Some color carries meaning: status, primary action, selection. All-gray is flagged.
  const chroma = (c) => { const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/); if (!m || (m[4] !== undefined && +m[4] < 0.2)) return 0; const v = [+m[1], +m[2], +m[3]]; return Math.max(...v) - Math.min(...v); };
  const colored = all.filter((el) => { const s = cs(el); return [s.color, s.backgroundColor, s.borderTopColor, s.fill].some((c) => chroma(c) >= 40); });
  check("no-color", colored.length ? [] : [{ el: "scope", detail: "no element uses a non-gray color" }], "WARN");

  // Component signatures, compared against a baseline page by ui_measure.py.
  const sigs = {};
  const add = (kind, el, s) => ((sigs[kind] ||= {})[s] ||= where(el));
  for (const el of all) {
    const s = cs(el), h = Math.round(box(el).height);
    if (el.matches(BUTTON) && !el.matches("[role=tab]")) add("button", el, `h ${h} radius ${s.borderTopLeftRadius} font ${s.fontSize}/${s.fontWeight}`);
    else if (el.matches(FIELD)) add("field", el, fieldSig(el));
    else if (el.matches("th, [role=columnheader]")) add("table-head", el, sig(el));
  }
  for (const g of groups) if (g.label === "table rows") { const hs = g.rows.map((r) => box(r).height).sort((a, b) => a - b); add("table-row", g.rows[0], `h ${Math.round(hs[hs.length >> 1])}`); }

  return { viewport: { width: vw, height: vh }, elements: all.length, coverage: Math.round(coverage * 100) / 100, checks, sigs };
}
