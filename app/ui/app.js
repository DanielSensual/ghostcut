// GhostCut Director — app UI. Talks to the local engine (app/engine.py) when it serves the page; opened as a plain
// file it falls back to window.PROJECT (demo/project.js) with scripted Muse replies. No build step.
(async () => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) el.setAttribute(k, v);
    }
    for (const k of kids.flat()) if (k != null) el.append(k.nodeType ? k : document.createTextNode(k));
    return el;
  };
  const fmt = (t) => {
    t = Math.max(0, t);
    const m = Math.floor(t / 60), s = Math.floor(t % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };
  const api = async (path, body) => {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  };

  // ---------- data: the engine if it's there, else the static demo ----------
  let P = window.PROJECT, LIVE = false, EDITS = [], ENGINE = {};
  if (location.protocol.startsWith("http")) {
    try { ({ project: P, edits: EDITS, engine: ENGINE } = await api("/api/project")); LIVE = true; } catch { /* static demo */ }
  }
  if (!P) {
    $("#noVideo").hidden = false;
    $("#projTitle").textContent = "No demo project";
    return;
  }
  const SEC = ["var(--sec0)", "var(--sec1)", "var(--sec2)", "var(--sec3)"];
  const thumb = (t) => `demo/thumbs/${String(Math.min(Math.floor(t / 2) + 1, Math.floor(P.duration / 2))).padStart(4, "0")}.jpg`;
  const pct = (t) => `${(t / P.duration) * 100}%`;
  const LIB = () => Object.fromEntries((P.library || []).map((e) => [e.tag, e]));
  const frameUrl = (tag) => {
    const e = LIB()[tag], b = e?.best || [0, 1];
    return `/api/frame?tag=${encodeURIComponent(tag)}&t=${((b[0] + b[1]) / 2 || 1).toFixed(1)}`;
  };
  const MANUAL = "inserted to cover the background";

  // ---------- top bar ----------
  $("#projTitle").textContent = P.title;
  $("#projSub").textContent = [P.client, P.shootDate && `shot ${P.shootDate}`, fmt(P.duration)].filter(Boolean).join(" · ");
  const spendBtn = $("#spendBtn"), pop = $("#spendPop");
  function renderSpend() {
    if (!P.spend) { spendBtn.hidden = true; return; }
    const muse = ENGINE.museSpent || 0, total = P.spend.total + muse;
    spendBtn.textContent = `$${total.toFixed(2)} Meta`;
    const rows = [...P.spend.rows];
    if (ENGINE.museCalls) rows.push(["Muse edit requests (Spark)", ENGINE.museCalls, muse]);
    pop.replaceChildren(
      h("h4", { style: "margin-top:0" }, "Meta API spend · this project"),
      h("table", {},
        ...rows.map(([what, n, usd]) => h("tr", {}, h("td", {}, what), h("td", {}, `×${n}`), h("td", {}, `$${usd.toFixed(2)}`))),
        h("tr", { class: "tot" }, h("td", {}, "Total, all versions"), h("td", {}), h("td", {}, `$${total.toFixed(2)}`))),
      h("p", { class: "muted", style: "margin:10px 0 0;font-size:11.5px" }, "Rows above the Muse line were entered by hand from the spend ledger. Camera originals stay on this Mac: Spark receives 360p proxies and transcripts. The optional voice-isolation step (mix --isolate) sends that audio to ElevenLabs."));
  }
  renderSpend();
  spendBtn.onclick = (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; };
  document.addEventListener("click", (e) => { if (!pop.contains(e.target)) pop.hidden = true; });
  $("#engineDot").classList.toggle("live", LIVE);
  $("#engineDot").title = LIVE ? "Engine connected: Muse runs on Spark" : "Static demo: Muse replies are scripted";

  // ---------- views ----------
  const rendered = {};
  const show = (v) => {
    $$(".view").forEach((el) => (el.hidden = el.id !== `view-${v}`));
    $$("#steps button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
    if (!rendered[v] && PAGES[v]) { PAGES[v]($(`#view-${v}`)); rendered[v] = true; }
    if (v === "edit") layout();
  };
  $$("[data-view]").forEach((b) => (b.onclick = () => show(b.dataset.view)));

  // ---------- player ----------
  const video = $("#video");
  video.poster = "demo/poster.jpg";
  video.src = "demo/video.mp4";
  video.onerror = () => ($("#noVideo").hidden = false);
  $("#tcDur").textContent = fmt(P.duration);
  const playBtn = $("#playBtn");
  const toggle = () => (video.paused ? video.play() : video.pause());
  playBtn.onclick = toggle;
  video.onplay = () => { playBtn.textContent = "❚❚"; tick(); };
  video.onpause = () => (playBtn.textContent = "▶");
  document.addEventListener("keydown", (e) => {
    if (/TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); toggle(); }
    if (e.code === "ArrowLeft") seek(video.currentTime - (e.shiftKey ? 5 : 1));
    if (e.code === "ArrowRight") seek(video.currentTime + (e.shiftKey ? 5 : 1));
  });
  const seek = (t) => { video.currentTime = Math.max(0, Math.min(P.duration - 0.05, t)); update(); };

  // ---------- sidebar ----------
  P.sections.forEach((s, i) => {
    $("#sectionList").append(h("li", { onclick: () => seek(s.t + 0.05), "data-i": i },
      h("span", { class: "dot", style: `background:${SEC[i % 4]}` }), s.name, h("span", { class: "len" }, fmt(s.d))));
  });
  (P.media || []).forEach((m) => {
    $("#binList").append(h("li", { class: m.flag ? "flag" : "" }, m.bin, h("span", { class: "n" }, m.count), h("span", { class: "note" }, m.note)));
  });

  // ---------- timeline ----------
  const scroller = $("#tracksScroll"), tracks = $("#tracks"), playhead = $("#playhead");
  let zoom = 1, selectedId = null;
  const place = (el, t, d) => { el.style.left = pct(t); el.style.width = pct(d); return el; };
  const select = (info) => {
    selectedId = info.id;
    $$(".clip.sel").forEach((x) => x.classList.remove("sel"));
    $(`.clip[data-id="${CSS.escape(info.id)}"]`)?.classList.add("sel");
    renderWhy(info); tab("why");
  };
  const PENDING = { drop: "dropped", change: "changed", add: "new" };

  function renderTimeline() {
    ["#trkSections", "#trkBroll", "#trkCards", "#trkTalk"].forEach((s) => $(s).replaceChildren());
    $$("#ruler .flagm").forEach((m) => m.remove());
    P.sections.forEach((s, i) => $("#trkSections").append(place(
      h("div", { class: "clip sec", style: `background:${SEC[i % 4]}`, title: s.name, onclick: () => seek(s.t + 0.05) }, s.name), s.t, s.d)));
    P.broll.forEach((b) => {
      const img = b.pending === "add" || b.pending === "change" ? (LIVE ? frameUrl(b.tag) : "") : thumb(b.t + b.d / 2);
      const el = place(h("div", {
        class: `clip br${b.pending ? ` p-${b.pending}` : ""}`, "data-id": b.id,
        title: `${b.tag}${b.pending ? ` · ${PENDING[b.pending]} · waiting to render` : ""}`,
        style: img ? `background-image:url(${img})` : "",
      }, b.tag), b.t, b.d);
      el.onclick = () => { select({ type: "broll", ...b }); seek(b.t + 0.1); };
      $("#trkBroll").append(el);
    });
    P.cards.forEach((c) => {
      const el = place(h("div", { class: `clip card${c.pending ? ` p-${c.pending}` : ""}`, "data-id": c.id, title: c.lines.join(" / ") }, c.lines.join(" · ")), c.t, c.d);
      el.onclick = () => { select({ type: "card", ...c }); seek(c.t + 0.3); };
      $("#trkCards").append(el);
    });
    P.talk.forEach((c) => {
      const el = place(h("div", { class: "clip tk", "data-id": c.id, title: `${c.topic} · ${c.src}` }, h("img", { src: thumb(c.t + c.d / 2), alt: "" }), h("span", {}, c.topic)), c.t, c.d);
      el.onclick = () => { select({ type: "talk", ...c }); seek(c.t + 0.05); };
      $("#trkTalk").append(el);
    });
    (P.cuts || []).forEach((c) => $("#trkTalk").append(place(
      h("div", { class: "cutspan", title: `Cut ${c.d.toFixed(2)} s · ${c.why || ""} · waiting to render`, onclick: () => seek(c.t - 1) }), c.t, Math.max(c.d, 0.4))));
    (P.findings || []).forEach((f, i) => {
      const m = h("div", { class: `flagm ${f.state}`, title: f.title, style: `left:${pct(f.t)}` });
      m.onclick = (e) => { e.stopPropagation(); tab("review"); pickFinding(i); };
      $("#ruler").append(m);
    });
    if (selectedId) $(`.clip[data-id="${CSS.escape(selectedId)}"]`)?.classList.add("sel");
    $("#pending").hidden = !P.pending;
    $("#pendingN").textContent = `${P.pending} change${P.pending === 1 ? "" : "s"}`;
  }

  const drawEnv = (canvas, data, color) => {
    if (!data) return;
    const w = canvas.clientWidth, hh = canvas.clientHeight, dpr = devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = hh * dpr;
    const g = canvas.getContext("2d");
    g.scale(dpr, dpr); g.fillStyle = color;
    const per = data.length / w;
    for (let x = 0; x < w; x++) {
      let v = 0;
      for (let k = Math.floor(x * per); k < Math.floor((x + 1) * per) + 1 && k < data.length; k++) v = Math.max(v, data[k]);
      const y = Math.max(1, v * v * (hh - 4));
      g.fillRect(x, (hh - y) / 2, 1, y);
    }
  };
  const ruler = () => {
    $$("#ruler .tick").forEach((t) => t.remove());
    const pxPerSec = tracks.clientWidth / P.duration;
    const step = [5, 10, 15, 30, 60, 120, 300].find((s) => s * pxPerSec >= 70) || 300;
    for (let t = 0; t < P.duration; t += step) $("#ruler").prepend(h("div", { class: "tick", style: `left:${pct(t)}` }, fmt(t)));
  };
  function layout() {
    tracks.style.width = `${scroller.clientWidth * zoom}px`;
    ruler();
    drawEnv($("#cvVoice"), P.env.voice, "#2dd4bf");
    drawEnv($("#cvMusic"), P.env.music, "#7c6cf2");
    update();
  }
  $("#zoom").oninput = (e) => {
    const center = video.currentTime / P.duration;
    zoom = +e.target.value; layout();
    scroller.scrollLeft = center * tracks.clientWidth - scroller.clientWidth / 2;
  };
  tracks.addEventListener("click", (e) => {
    if (e.target.closest(".clip, .flagm, .cutspan")) return;
    const r = tracks.getBoundingClientRect();
    seek(((e.clientX - r.left) / r.width) * P.duration);
  });
  addEventListener("resize", layout);

  let segI = -1, wordEl = null;
  function update() {
    const t = video.currentTime || 0;
    $("#tcNow").textContent = fmt(t);
    playhead.style.left = pct(t);
    const s = P.sections.findIndex((x) => t >= x.t && t < x.t + x.d);
    const c = P.talk.find((x) => t >= x.t && t < x.t + x.d);
    $("#where").textContent = [P.sections[s]?.name, c?.topic].filter(Boolean).join(" › ");
    $$("#sectionList li").forEach((li, i) => li.classList.toggle("on", i === s));
    if (zoom > 1) { // keep the playhead in view after seeks and while playing
      const x = (t / P.duration) * tracks.clientWidth;
      if (x < scroller.scrollLeft + 40 || x > scroller.scrollLeft + scroller.clientWidth - 80) scroller.scrollLeft = x - 120;
    }
    followWords(t);
  }
  function tick() { update(); if (!video.paused) requestAnimationFrame(tick); }
  video.ontimeupdate = update;

  // ---------- right panel ----------
  function tab(name) {
    $$("#panelTabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    $$(".panel .tab").forEach((el) => (el.hidden = el.id !== `tab-${name}`));
    if (name === "words") followWords(video.currentTime || 0, true);
  }
  $$("#panelTabs button").forEach((b) => (b.onclick = () => tab(b.dataset.tab)));

  // Muse
  const chat = $("#chat");
  const scrollChat = () => (chat.scrollTop = chat.scrollHeight);
  const museMsg = (m) => {
    const el = h("div", { class: `msg muse${m.scripted ? " scripted" : ""}` },
      h("div", { class: "who" }, "Muse", m.scripted ? h("span", { class: "tag" }, "SCRIPTED EXAMPLE") : null), h("div", {}, m.text));
    if (m.changes) el.append(h("div", { class: "changes" }, m.changes.map(([a, b]) => h("div", {}, h("b", {}, a), h("span", {}, b)))));
    if (m.verify) el.append(m.scripted ? h("div", { class: "note" }, m.verify) : h("div", { class: `verify${m.model ? " model" : ""}` }, m.verify));
    if (m.steps) el.append(h("ol", { class: "thinking" }, m.steps.map((s) => h("li", {}, s))));
    const acts = h("div", { class: "acts" });
    if (m.seek != null) acts.append(h("button", { onclick: () => seek(m.seek) }, `Show ${fmt(m.seek)}`));
    if (m.proposal) {
      const ok = h("button", { class: "ok" }, "Accept");
      const no = h("button", {}, "Reject");
      ok.onclick = () => acts.replaceChildren(h("span", { class: "applied" }, "Accepted (static demo: nothing changes)"));
      no.onclick = () => acts.replaceChildren(h("span", { class: "applied", style: "color:var(--dim)" }, "Rejected · kept as is"));
      acts.append(ok, no);
    } else if (m.changes && !m.scripted) acts.append(h("span", { class: "applied" }, "✓ Applied"));
    if (acts.children.length) el.append(acts);
    return el;
  };
  const addMsg = (m) => { chat.append(m.who === "you" ? h("div", { class: `msg you${m.scripted ? " scripted" : ""}` }, m.text) : museMsg(m)); scrollChat(); };
  // The opening conversation is hand-written demo content (from the --extra file), not Muse output: say so.
  if (P.chat?.length) chat.append(h("div", { class: "divider" }, "Scripted example · written by hand during development, not produced by Muse"));
  (P.chat || []).forEach((m) => addMsg({ ...m, scripted: true }));
  if (P.chat?.length) chat.append(h("div", { class: "divider" }, LIVE ? "Live from here · Muse runs on Spark" : "Static demo · replies below are scripted too"));

  function applyResult(j) {
    ({ project: P, edits: EDITS, engine: ENGINE } = j);
    renderTimeline(); renderFindings(); renderSpend();
  }

  function proposalMsg(r) {
    const el = h("div", { class: "msg muse" }, h("div", { class: "who" }, "Muse"), h("div", {}, r.reply));
    if (r.answers?.length) el.append(h("div", { class: "answers" }, r.answers.map((a) => h("button", { onclick: () => seek(a.t) }, `${fmt(a.t)} · ${a.label}`))));
    const picks = [];
    if (r.ops?.length) {
      el.append(h("div", { class: "ops" }, r.ops.map((op) => {
        const cb = h("input", { type: "checkbox", checked: !op.blocked || null, disabled: op.blocked || null });
        picks.push(cb);
        const shot = /^(swap|add)_broll$/.test(op.op) && LIB()[op.src] ? h("img", { class: "opframe", src: frameUrl(op.src), alt: "", loading: "lazy" }) : null;
        const at = op.t >= 0 ? op.t : (P.broll.concat(P.cards).find((x) => x.id === op.target)?.t ?? null);
        return h("label", { class: `op${op.blocked ? " blocked" : ""}` }, cb,
          h("div", {},
            h("b", {}, op.label), at != null ? h("a", { class: "go", onclick: (e) => { e.preventDefault(); seek(at); } }, fmt(at)) : null,
            h("div", { class: "why" }, op.why), shot,
            h("ul", { class: "opchecks" }, op.checks.map((c) => h("li", { class: c.ok ? "ok" : c.hard ? "bad" : "warn" }, c.text)))));
      })));
    }
    el.append(h("div", { class: "meta" }, `◆ ${r.model} · ${r.cached ? "cached · $0.00" : `$${r.usd.toFixed(3)} · ${Math.round(r.seconds)} s`} · checks are measured`));
    const acts = h("div", { class: "acts" });
    if (r.ops?.some((o) => !o.blocked)) {
      const ok = h("button", { class: "ok" }, "Accept");
      const no = h("button", {}, "Reject");
      ok.onclick = async () => {
        const pick = picks.map((c, i) => (c.checked ? i : -1)).filter((i) => i >= 0);
        ok.disabled = true;
        try {
          const j = await api("/api/accept", { id: r.id, pick });
          applyResult(j);
          const edit = j.edits[j.edits.length - 1];
          picks.forEach((c) => (c.disabled = true));
          const undo = h("button", {}, "Undo");
          undo.onclick = async () => {
            applyResult(await api("/api/undo", { edit: edit.id }));
            acts.replaceChildren(h("span", { class: "applied", style: "color:var(--dim)" }, "Undone"));
          };
          acts.replaceChildren(h("span", { class: "applied" }, `✓ Applied ${pick.length} · waiting to render`), undo);
        } catch (e) { ok.disabled = false; acts.append(h("span", { class: "err" }, e.message)); }
      };
      no.onclick = () => { picks.forEach((c) => (c.disabled = true)); acts.replaceChildren(h("span", { class: "applied", style: "color:var(--dim)" }, "Rejected · kept as is")); };
      acts.append(ok, no);
    } else if (r.ops?.length) acts.append(h("span", { class: "applied", style: "color:var(--red)" }, "Every change was blocked by a check"));
    if (acts.children.length) el.append(acts);
    return el;
  }

  async function askLive(text) {
    addMsg({ who: "you", text });
    const started = Date.now();
    const status = h("span", {}, "Spark is reading the cut…");
    const think = h("div", { class: "msg muse" }, h("div", { class: "who" }, "Muse"), h("div", { class: "thinking" }, status));
    chat.append(think); scrollChat();
    const timer = setInterval(() => (status.textContent = `Spark is reading the cut, transcript and shot library · ${Math.round((Date.now() - started) / 1000)} s`), 1000);
    try {
      const r = await api("/api/muse", { text, at: video.currentTime || 0 });
      think.replaceWith(proposalMsg(r));
      ENGINE.museSpent = (ENGINE.museSpent || 0) + (r.usd || 0);
      ENGINE.museCalls = (ENGINE.museCalls || 0) + (r.cached ? 0 : 1);
      renderSpend();
    } catch (e) {
      think.replaceWith(museMsg({ text: `That didn't go through: ${e.message}` }));
    } finally { clearInterval(timer); scrollChat(); }
  }

  // Static demo only: canned replies so the page still shows the flow without the engine.
  const REPLIES = [
    [/export|final cut|resolve|premiere|deliver/i, () => { setTimeout(() => show("deliver"), 700); return { text: "Opening Deliver." }; }],
  ];
  const askStatic = (text) => {
    addMsg({ who: "you", text });
    const hit = REPLIES.find(([re]) => re.test(text));
    addMsg(hit ? { who: "muse", ...hit[1]() } : { who: "muse", text: "This page is the static demo. Run app/engine.py (or app/main.py) and Muse answers through Spark." });
  };
  const reply = (text) => (LIVE ? askLive(text) : askStatic(text));
  (P.suggestions || []).forEach((s) => $("#suggest").append(h("button", { onclick: () => reply(s) }, s)));
  $("#askForm").onsubmit = (e) => {
    e.preventDefault();
    const v = $("#askInput").value.trim();
    if (v) { $("#askInput").value = ""; reply(v); }
  };
  $("#askInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#askForm").requestSubmit(); } });
  $("#undoLast").onclick = async () => { if (LIVE && EDITS.length) applyResult(await api("/api/undo", {})); };

  // Review
  let filter = "all";
  const SRC = { measured: "Measured", spark: "Spark", you: "You", reshoot: "Reshoot" };
  function renderFindings() {
    const list = $("#findings"); list.replaceChildren();
    if ((P.findings || []).some((f) => !f.edit))
      list.append(h("li", { class: "banner" }, "Findings without a Muse note were written by hand during development (scripted demo content), not generated by the app."));
    (P.findings || []).forEach((f, i) => {
      if (filter !== "all" && f.state !== filter && !(filter === "fixed" && f.state === "ok")) return;
      list.append(h("li", { "data-i": i, onclick: () => pickFinding(i) },
        h("div", { class: "fhead" }, h("span", { class: `b ${f.src}` }, SRC[f.src]), h("span", { class: `state ${f.state}` }, f.state), h("span", { class: "t" }, fmt(f.t))),
        h("div", { class: "ftitle" }, f.title), h("div", { class: "fbody" }, f.body), h("div", { class: "fcheck" }, f.check)));
    });
    const open = (P.findings || []).filter((f) => f.state === "open").length;
    $("#openCount").textContent = open || "";
  }
  function pickFinding(i) {
    $$("#findings li").forEach((li) => li.classList.toggle("sel", +li.dataset.i === i));
    seek(P.findings[i].t);
  }
  $$("#filters button").forEach((b) => (b.onclick = () => {
    filter = b.dataset.f;
    $$("#filters button").forEach((x) => x.classList.toggle("on", x === b));
    renderFindings();
  }));

  // Words: the Whisper transcript of the finished cut, following the playhead.
  const T = P.transcript || [];
  function renderWords(q = "") {
    const box = $("#words"); box.replaceChildren();
    q = q.trim().toLowerCase();
    let hits = 0;
    T.forEach((s, i) => {
      if (q && !s.text.toLowerCase().includes(q)) return;
      hits++;
      box.append(h("div", { class: "seg", "data-i": i },
        h("button", { class: "st", onclick: () => seek(s.t) }, fmt(s.t)),
        h("p", {}, s.w.map((w) => h("span", { class: `w${q && w[0].toLowerCase().includes(q) ? " hit" : ""}`, "data-s": w[1], onclick: () => seek(w[1] + 0.01) }, `${w[0]} `)))));
    });
    $("#wordsCount").textContent = q ? `${hits} of ${T.length} lines` : `${T.length} lines · ${T.reduce((a, s) => a + s.w.length, 0)} words · Whisper on the finished cut`;
    segI = -1;
  }
  function followWords(t, force) {
    if ($("#tab-words").hidden) return;
    let i = T.findIndex((s) => t >= s.t && t < s.e + 0.3);
    if (i !== segI || force) {
      $$("#words .seg.on").forEach((x) => x.classList.remove("on"));
      const row = $(`#words .seg[data-i="${i}"]`);
      row?.classList.add("on");
      if (row && (!video.paused || force)) row.scrollIntoView({ block: "nearest" });
      segI = i;
    }
    if (i < 0) return;
    const w = T[i].w.findIndex((x) => t >= x[1] && t < x[2]);
    const el = w >= 0 ? $(`#words .seg[data-i="${i}"] .w:nth-child(${w + 1})`) : null;
    if (el !== wordEl) { wordEl?.classList.remove("now"); el?.classList.add("now"); wordEl = el; }
  }
  $("#wordsSearch").oninput = (e) => renderWords(e.target.value);

  // Why
  function renderWhy(c) {
    const why = $("#why"); why.replaceChildren();
    const kv = (rows) => h("div", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v]) => [h("b", {}, k), h("span", {}, v)]));
    const lib = LIB();
    const newShot = c.type === "broll" && (c.pending === "add" || c.pending === "change") && LIVE;
    why.append(h("img", { class: "frame", src: newShot ? frameUrl(c.tag) : thumb(c.t + Math.min(c.d / 2, 3)), alt: "" }));
    if (c.pending) why.append(h("div", { class: `pendnote p-${c.pending}` }, `${PENDING[c.pending]} · waiting to render`, c.why ? ` · ${c.why}` : ""));
    if (c.type === "talk") {
      why.append(h("h3", {}, c.topic), kv([["Section", c.section], ["Source", c.src], ["Source in", `${c.in.toFixed(2)} s`], ["Length", `${c.d.toFixed(2)} s`], ["Timeline", fmt(c.t)]]));
      const rows = (P.takes || []).filter((t) => t.topic === c.topic);
      if (rows.length) {
        const pick = rows.find((t) => t.kind === "keeper_take") || rows[0];
        why.append(h("div", { class: "reason" }, h("small", {}, `Spark · ${pick.kind.replace(/_/g, " ")} · confidence ${pick.conf ?? "–"}`), pick.reason));
        why.append(h("h4", {}, `Takes of this moment (${rows.length})`));
        rows.forEach((t) => why.append(h("div", { class: `alt${t.kind === "keeper_take" ? " pick" : ""}` }, `${t.clip} @ ${fmt(t.in)} · ${t.kind.replace(/_/g, " ")} — ${t.reason}`)));
      } else {
        why.append(h("div", { class: "reason" }, h("small", {}, "Measured"), "Cut points set from the 20 ms loudness envelope, in silences; Whisper on the render confirms no clipped words."));
      }
      const said = T.filter((s) => s.e > c.t && s.t < c.t + c.d).map((s) => s.text).join(" ");
      if (said) why.append(h("h4", {}, "What she says"), h("p", { class: "said" }, `“${said.slice(0, 420)}${said.length > 420 ? "…" : ""}”`));
    } else if (c.type === "broll") {
      const e = lib[c.tag] || {};
      why.append(h("h3", {}, c.tag), kv([["Shot", e.what || "—"], c.was_tag && ["Was", c.was_tag], ["Section", c.section], ["Timeline", `${fmt(c.t)} · ${c.d.toFixed(1)} s`], e.q != null && ["Quality", `${e.q}`]]));
      const manual = c.reason === MANUAL;
      const byMuse = c.pending === "add" || c.was_tag; // swaps and additions carry Muse's reason; moves keep the original one
      why.append(h("div", { class: "reason" }, h("small", {}, byMuse ? "Muse · your accepted change" : manual ? "Manual insert" : "Spark · B-roll plan"),
        manual ? "Placed by hand (an --insert during development), not by Spark." : c.reason || "—"));
      const words = T.filter((s) => s.e > c.t && s.t < c.t + c.d).map((s) => s.text).join(" ");
      if (words) why.append(h("h4", {}, "Under the words"), h("p", { class: "said" }, `“${words.slice(0, 300)}”`));
    } else if (c.type === "card") {
      why.append(h("h3", {}, c.lines.join(" · ")), kv([["Caption", c.caption || "—"], c.was_lines && ["Was", c.was_lines.join(" · ")], ["On screen", `${fmt(c.t)} · ${c.d.toFixed(1)} s`]]));
      const said = T.filter((s) => s.e > c.t - 6 && s.t < c.t + 3).map((s) => s.text).join(" ");
      why.append(h("div", { class: "reason" }, h("small", {}, "How it was made"), "Card text and timing were written by hand from the transcript. Cards that Muse adds or edits are checked against the transcript (numbers must be said within ±20 s)."));
      if (said) why.append(h("h4", {}, "Said on camera"), h("p", { class: "said" }, `“${said.slice(0, 300)}”`));
    }
  }

  // ---------- pages ----------
  const stat = (n, label) => h("div", { class: "stat" }, h("b", {}, n), h("span", {}, label));
  const PAGES = {
    story(el) {
      const TK = P.takes || [];
      const count = (k) => TK.filter((t) => t.kind === k).length;
      el.append(h("h2", {}, "Story"),
        h("p", { class: "lede" }, "Spark watched the house walkthrough with numbered transcript lines beside it and sorted every moment. Whisper keeps the clock. (The other sections' takes were picked by hand, and reclassifying here is not saved yet.)"),
        h("div", { class: "stats" }, stat(TK.length, "moments"), stat(count("keeper_take"), "keepers"), stat(count("alternate_take"), "alternates"),
          stat(count("flub_or_false_start"), "flubs"), stat(count("aside_to_editor"), "asides to the editor"), stat(count("setup_or_technical") + count("off_camera_or_pause"), "setup & pauses"),
          P.takeMeta?.model && stat(`$${(+P.takeMeta.usd).toFixed(2)}`, `${P.takeMeta.model} · ${Math.round(P.takeMeta.seconds)} s`)));
      const groups = [];
      TK.forEach((t) => { let g = groups.find((x) => x.topic === t.topic); if (!g) groups.push((g = { topic: t.topic, rows: [] })); g.rows.push(t); });
      const KINDS = ["keeper_take", "alternate_take", "flub_or_false_start", "aside_to_editor", "setup_or_technical", "off_camera_or_pause"];
      groups.forEach((g) => {
        const keep = g.rows.filter((r) => r.kind === "keeper_take").length;
        el.append(h("div", { class: "group" },
          h("header", {}, h("b", {}, g.topic), h("span", {}, `${g.rows.length} moment${g.rows.length > 1 ? "s" : ""} · ${keep} kept`)),
          g.rows.map((t) => {
            const row = h("div", { class: `take${t.kind === "keeper_take" ? "" : " drop"}` },
              h("span", { class: "star" }, t.kind === "keeper_take" ? "★" : ""),
              h("select", { onchange: (e) => row.classList.toggle("drop", e.target.value !== "keeper_take") },
                KINDS.map((k) => h("option", { value: k, selected: k === t.kind || null }, k.replace(/_/g, " ")))),
              h("span", { class: "mono" }, `${t.clip} ${fmt(t.in)}`), h("span", { class: "mono" }, `${t.d.toFixed(1)} s`),
              h("span", {}, t.reason), h("span", { class: "said" }, `“${t.text.slice(0, 150)}${t.text.length > 150 ? "…" : ""}”`));
            return row;
          })));
      });
    },
    import(el) {
      const total = (P.media || []).reduce((a, m) => a + m.count, 0);
      el.append(h("h2", {}, "Import"),
        h("p", { class: "lede" }, "Planned: point GhostCut at a card or folder, confirm the shoot date, and it scans every mounted drive for clips recorded that day. NOT BUILT YET: this page shows hand-written sample data."),
        h("div", { class: "stats" }, stat(P.shootDate || "—", "shoot date"), stat(total, "clips & assets"), stat((P.media || []).length, "bins"), stat(P.sections.length, "sections proposed")),
        h("div", { class: "cols" },
          P.importFlag && h("div", { class: "box flag" }, h("h3", {}, `⚑ ${P.importFlag.title}`),
            h("p", {}, P.importFlag.body),
            h("div", { class: "vol" }, P.importFlag.where, h("span", {}, P.importFlag.clips))),
          h("div", { class: "box" }, h("h3", {}, "Bins"), (P.media || []).map((m) => h("div", { class: "vol" }, m.bin, h("span", {}, m.count)))),
          h("div", { class: "box" }, h("h3", {}, "What happens next"),
            h("ol", { class: "thinking", style: "padding-left:18px" }, ["360p proxies for every clip (originals stay here)", "Whisper words for every talking take", "Spark inventory: what each clip shows and where it belongs", "Sections proposed from the day's order"].map((s) => h("li", {}, s))))));
    },
    deliver(el) {
      const { lufs, tp } = P.loudness || { lufs: NaN, tp: NaN };
      const pos = (v) => `${((v + 24) / 24) * 100}%`;
      el.append(h("h2", {}, "Deliver"),
        h("p", { class: "lede" }, "Finish here, or hand the cut to your editor with every decision editable. PROTOTYPE: the export buttons are not wired yet; the loudness figures were measured on the current master."),
        h("div", { class: "cols" },
          h("div", { class: "box" }, h("h3", {}, "Outputs"),
            [["Master · 1080p59.94 MP4", "−14 LUFS web master"], ["Phone preview · 540p", "under 30 MB, for the client's phone"], ["Final Cut Pro · FCPXML", "cuts, B-roll lane, cards as titles"], ["Premiere Pro · XML", "FCP7 XML"], ["DaVinci Resolve", "imports the FCPXML"], ["Captions · SRT", "from Whisper words on the render"], ["Reshoot list", "missing shots, never generated"]]
              .map(([a, b], i) => h("label", { class: "out" }, h("input", { type: "checkbox", checked: i < 3 || null }), h("div", {}, h("b", {}, a), h("span", {}, b))))),
          h("div", { class: "box" }, h("h3", {}, "Loudness"),
            h("div", { class: "big" }, `${lufs} LUFS`), h("p", {}, `True peak ${tp.toFixed(1)} dBTP · target −14 LUFS ±1, ≤ −1 dBTP`),
            h("div", { class: "meter" }, h("div", { class: "tgt", style: `left:${pos(-15)};right:${`${100 - parseFloat(pos(-13))}%`}` }), h("i", { style: `left:${pos(lufs)}` })),
            h("p", { class: "muted", style: "font-size:11px" }, "−24 … 0 LUFS · measured with ffmpeg ebur128")),
          h("div", { class: "box" }, h("h3", {}, "Open before you send"),
            (P.findings || []).filter((f) => f.state === "open").map((f) => h("div", { class: "vol" }, f.title, h("span", {}, fmt(f.t)))),
            P.pending ? h("div", { class: "vol" }, `${P.pending} accepted change${P.pending === 1 ? "" : "s"} waiting to render`, h("span", {}, "Edit")) : null),
        ),
        h("p", {}, h("button", { class: "primary", style: "margin-top:16px", onclick: (e) => (e.target.textContent = "Exporting… (prototype)") }, "Export selected")));
    },
  };

  renderTimeline();
  renderFindings();
  renderWords();
  const start = location.hash.slice(1).split("/");
  show(PAGES[start[0]] ? start[0] : "edit");
  tab(["review", "words", "why"].includes(start[1]) ? start[1] : "muse");
  // Open on a chosen frame (the cold open's title card) rather than a black first frame.
  if (P.startAt) video.addEventListener("loadedmetadata", () => seek(P.startAt), { once: true });
})();
