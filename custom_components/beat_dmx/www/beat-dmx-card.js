// Beat DMX live view: the DMX values going out to each fixture Beat DMX controls, pushed ~5x/s
// over the websocket (beat_dmx/subscribe). Loaded as the Lovelace resource /local/beat_dmx/beat-dmx-card.js (copied there by the integration). Channel meanings: BeamZ Panther 40 LED Spot, 11-channel mode.
// Options: fixtures: [prefix, ...] shows those fixtures in that order (others after them);
//          layout: stacked puts the fixtures under each other instead of side by side;
//          manual_entity: the Manual DMX switch (switch.beat_dmx_manual_dmx by default);
//          title: the card's heading ("Live DMX" by default);
//          blackout_entity / strobe_entity: the Blackout / Strobe switches (switch.beat_dmx_blackout / _strobe by default).
// With Manual DMX on, the bars become sliders and the pan/tilt pad can be dragged (beat_dmx/set_channels),
// and each head can be calibrated from where it points (beat_dmx.calibrate).

const range = (v, table) => {
  for (const [hi, text] of table) if (v <= hi) return typeof text === "function" ? text(v) : text;
  return "";
};
const pct = (v, lo = 0, hi = 255) => `${Math.round(((v - lo) / (hi - lo)) * 100)}%`;
// the colour wheel: white + 7 colours, as the integration says they look (live data `colours`)
let WHEEL = ["White", "Colour 1", "Colour 2", "Colour 3", "Colour 4", "Colour 5", "Colour 6", "Colour 7"].map((name) => ({ name, hex: "#ffffff" }));
// channel 5 -> the wheel slot(s) lit: one slot (0-56), two halves (57-127), or turning (128-255: slot by time)
const wheelSlots = (v) => {
  if (v <= 56) return [v <= 7 ? 0 : Math.min(7, Math.ceil((v - 7) / 7))];
  if (v <= 127) { const k = Math.min(6, Math.floor((v - 57) / 10)); return [k, k + 1]; }
  return null;
};
const colourName = (v) => {
  const slots = wheelSlots(v);
  if (slots) return slots.map((i) => WHEEL[i].name).join(" + ");
  return range(v, [[189, (x) => `Rotate ${pct(189 - x, 0, 61)} fast`], [193, "Rotation stop"], [255, (x) => `Rotate ${pct(x, 194, 255)} fast`]]);
};
// CSS background for the dot: the colour, half and half when split, cycling while the wheel turns
const colourCss = (v) => {
  const slots = wheelSlots(v) || [Math.floor(performance.now() / 500) % WHEEL.length];
  const [a, b] = slots.map((i) => WHEEL[i].hex);
  return { bg: b ? `linear-gradient(90deg, ${a} 50%, ${b} 50%)` : a, glow: a };
};
const GOBO = [
  [7, "Open"], ...[1, 2, 3, 4, 5, 6, 7].map((g) => [7 + g * 8, `Gobo ${g}`]), [71, "Open"],
  ...[1, 2, 3, 4, 5, 6, 7].map((g) => [71 + g * 8, `Gobo ${g} shake`]),
  [189, (v) => `Rotate ${pct(189 - v, 0, 61)} fast`], [193, "Rotation stop"], [255, (v) => `Rotate ${pct(v, 194, 255)} fast`],
];
const SHUTTER = [
  [7, "Closed"], [15, "Open"], [131, (v) => `Strobe ${pct(v, 16, 131)}`], [139, "Open"], [181, "Pulse close"],
  [189, "Open"], [231, "Pulse open"], [239, "Open"], [247, "Random strobe"], [255, "Open"],
];
const FUNCTION = [
  [69, "None"], [79, "Blackout on move"], [89, "None"], [99, "Blackout on colour"], [109, "None"],
  [119, "Blackout on gobo"], [199, "None"], [209, "Reset"], [249, "None"], [255, "Sound active"],
];
const DIM_MODE = [[20, "Standard"], [40, "Stage"], [60, "TV"], [80, "Architectural"], [255, "Theatre"]];

const CHANNELS = [
  { label: "Pan", decode: (v) => `${Math.round((v / 255) * 540)}°` },
  { label: "Pan fine", decode: () => "" },
  { label: "Tilt", decode: (v) => `${Math.round((v / 255) * 270)}°` },
  { label: "Tilt fine", decode: () => "" },
  { label: "Colour", decode: (v) => colourName(v) },
  { label: "Gobo", decode: (v) => range(v, GOBO) },
  { label: "Shutter", decode: (v) => range(v, SHUTTER) },
  { label: "Dimmer", decode: (v) => pct(v) },
  { label: "P/T speed", decode: (v) => pct(v) },
  { label: "Function", decode: (v) => range(v, FUNCTION) },
  { label: "Dim mode", decode: (v) => range(v, DIM_MODE) },
];

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// what the tempo sensor's source means, and whether that counts as found
const TEMPO_SOURCES = [
  [/^analysis/, "Measured from the track", "ok"],
  [/^getsongbpm/, "Found on GetSongBPM", "ok"],
  [/^deezer isrc/, "Found on Deezer (exact track)", "ok"],
  [/^deezer search/, "Found on Deezer (search)", "ok"],
  [/^tap/, "Tapped in", "ok"],
  [/^manual/, "Tempo override", "ok"],
  [/looking up/, "Looking it up…", ""],
  [/not found/, "Not found – using the default tempo", "warn"],
  [/^default/, "Default tempo", ""],
];
const tempoStatus = (source) => {
  const s = String(source || "");
  const hit = TEMPO_SOURCES.find(([re]) => re.test(s)) || [null, s, ""];
  return { text: hit[1] + (/live takes only/.test(s) ? " – live versions only, may be off" : ""), cls: hit[2] };
};

// Section show: the colour of each section on the timeline
const SECTION_COLOURS = { Intro: "#78909c", Main: "#42a5f5", Breakdown: "#ab47bc", "Build-up": "#ffa726",
  "Quiet gap": "#212121", Drop: "#ef5350", Outro: "#78909c" };
const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

const SEND_MS = 150; // manual DMX: at most one message per fixture this often while dragging
const HOLD_MS = 600; // and the slider is left alone for this long after it was moved

class BeatDmxCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    this._safe(() => { if (!this._unsub && !this._subscribing && this.isConnected) this._subscribe(); });
  }

  connectedCallback() {
    this._safe(() => {
      if (!this.shadowRoot) this._build();
      if (this._hass && !this._unsub && !this._subscribing) this._subscribe();
    });
  }

  // runs fn; an error is shown in the card (and the console) instead of breaking the card
  _safe(fn) {
    try {
      return fn();
    } catch (err) {
      console.error("beat-dmx-card", err);
      if (this._els && this._els.err) {
        this._els.err.hidden = false;
        this._els.err.textContent = `Card error: ${err.message || err}`;
      }
      return undefined;
    }
  }

  disconnectedCallback() {
    if (this._unsub) this._unsub.then((u) => u()).catch(() => {});
    this._unsub = null;
    if (this._beatTimer) clearInterval(this._beatTimer);
    this._beatTimer = null;
    if (this._tlTimer) clearInterval(this._tlTimer);
    this._tlTimer = null;
    if (this._big) this._big.close();
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _subscribe() {
    this._subscribing = true;
    this._unsub = this._hass.connection
      .subscribeMessage((data) => this._safe(() => this._render(data)), { type: "beat_dmx/subscribe" })
      .catch((err) => {
        this._unsub = null;
        this._safe(() => this._render({ error: err.message || "Beat DMX is not available" }));
        return () => {};
      })
      .finally(() => (this._subscribing = false));
  }

  // ---- manual DMX -------------------------------------------------------------------------
  _send(fixture, channel, value) {
    const key = `${fixture}/${channel}`;
    this._touched.set(key, Date.now());
    this._pending.set(key, { fixture, channel, value: Math.round(value) });
    if (!this._timer) this._timer = setTimeout(() => this._flush(), SEND_MS);
  }

  _flush() {
    this._timer = null;
    if (!this._hass) return;
    // one websocket message per fixture with all its changed channels (beat_dmx/set_channels)
    const byFixture = new Map();
    this._pending.forEach(({ fixture, channel, value }) => {
      if (!byFixture.has(fixture)) byFixture.set(fixture, {});
      byFixture.get(fixture)[channel] = value;
    });
    this._pending.clear();
    byFixture.forEach((values, fixture) =>
      this._hass.connection.sendMessagePromise({ type: "beat_dmx/set_channels", fixture, values }).catch((e) => this._toast(e)));
  }

  _held(fixture, channel) {
    return Date.now() - (this._touched.get(`${fixture}/${channel}`) || 0) < HOLD_MS;
  }

  _toast(err) {
    if (this._els && this._els.err) {
      this._els.err.hidden = false;
      this._els.err.textContent = `Beat DMX: ${err.message || err}`;
      clearTimeout(this._errTimer);
      this._errTimer = setTimeout(() => (this._els.err.hidden = true), 8000);
    }
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: `Beat DMX: ${err.message || err}` }, bubbles: true, composed: true }));
  }

  _toggleManual() {
    const entity_id = this._config.manual_entity || "switch.beat_dmx_manual_dmx";
    const on = this._d && this._d.manual;
    this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id }).catch((e) => this._toast(e));
  }

  _calibrate(fixture, point) {
    this._hass.callService("beat_dmx", "calibrate", { fixture, point }).catch((e) => this._toast(e));
  }

  // ---- layout -----------------------------------------------------------------------------
  _build() {
    this._pending = new Map();
    this._touched = new Map();
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { --bd-accent: var(--primary-color, #03a9f4); --bd-muted: var(--secondary-text-color, #888);
                --bd-track: color-mix(in srgb, var(--primary-text-color, #fff) 10%, transparent);
                --bd-ok: var(--success-color, #4caf50); --bd-warn: var(--warning-color, #ff9800); }
        ha-card { padding: 16px; }
        .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .title { font-size: 18px; font-weight: 500; flex: 1; min-width: 120px; }
        .chip { font-size: 12px; padding: 2px 10px; border-radius: 12px; background: var(--bd-track); color: var(--bd-muted); }
        .chip.on { background: color-mix(in srgb, var(--bd-ok) 22%, transparent); color: var(--bd-ok); }
        .chip.manual { background: color-mix(in srgb, var(--bd-warn) 22%, transparent); color: var(--bd-warn); }
        button { font: inherit; font-size: 12px; padding: 4px 10px; border-radius: 14px; cursor: pointer;
                 border: 1px solid var(--divider-color, #444); background: transparent; color: var(--primary-text-color); }
        button:hover { border-color: var(--bd-accent); }
        button.active { background: var(--bd-warn); border-color: var(--bd-warn); color: #000; }
        button#blackout.active { background: #000; border-color: #000; color: #fff; }
        button#strobe.active { background: #fff; border-color: #fff; color: #000; box-shadow: 0 0 10px #fff; }
        .chip.dark { background: #000; color: #fff; }
        .tempo { display: flex; align-items: baseline; gap: 6px; }
        .bpm { font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
        .unit { color: var(--bd-muted); font-size: 12px; }
        .beats { display: flex; gap: 6px; }
        .beats i { width: 12px; height: 12px; border-radius: 50%; background: var(--bd-track); transition: background 60ms; }
        .beats i.lit { background: var(--bd-accent); box-shadow: 0 0 8px var(--bd-accent); }
        .info { margin: 8px 0 12px; display: grid; grid-template-columns: 64px 1fr; gap: 4px 8px; font-size: 13px; }
        .info dt { color: var(--bd-muted); }
        .info dd { margin: 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .found::before { content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
                         background: var(--bd-muted); vertical-align: 1px; }
        .found.ok::before { background: var(--bd-ok); } .found.warn::before { background: var(--bd-warn); }
        .fixtures { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
        .fixtures.stacked { grid-template-columns: 1fr; }
        .fx { border: 1px solid var(--divider-color, #333); border-radius: 12px; padding: 12px; }
        .fx h3 { margin: 0 0 10px; font-size: 15px; font-weight: 500; display: flex; gap: 8px; align-items: center; }
        .fx h3 span { color: var(--bd-muted); font-size: 12px; font-weight: 400; }
        .pad { position: relative; height: 90px; border-radius: 8px; background: var(--bd-track); margin-bottom: 10px;
               overflow: hidden; touch-action: none; }
        .manual .pad { height: 150px; cursor: crosshair; outline: 1px dashed var(--bd-warn); }
        .pad::before, .pad::after { content: ""; position: absolute; background: color-mix(in srgb, var(--bd-muted) 30%, transparent); }
        .pad::before { left: 50%; top: 0; bottom: 0; width: 1px; } .pad::after { top: 50%; left: 0; right: 0; height: 1px; }
        .dot { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%; pointer-events: none;
               background: #fff; transition: left 200ms linear, top 200ms linear, opacity 200ms linear, box-shadow 200ms linear; }
        .padlabel { position: absolute; right: 8px; bottom: 4px; font-size: 11px; color: var(--bd-muted); pointer-events: none; }
        .padhint { position: absolute; left: 8px; top: 4px; font-size: 11px; color: var(--bd-warn); display: none; pointer-events: none; }
        .manual .padhint { display: block; }
        .cal { display: none; margin: -2px 0 10px; font-size: 12px; color: var(--bd-muted); }
        .manual .cal { display: block; }
        .cal .btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
        .cal ol { margin: 6px 0; padding-left: 20px; } .cal li { margin: 4px 0; line-height: 1.9; }
        .cal button.set { border-color: var(--bd-warn); }
        .cal b { color: var(--primary-text-color); font-weight: 500; }
        .row { display: grid; grid-template-columns: 74px 30px 1fr 30px minmax(0, 104px); gap: 8px; align-items: center;
               font-size: 12px; height: 24px; }
        .row .addr, .row .val { color: var(--bd-muted); font-variant-numeric: tabular-nums; text-align: right; }
        .row .val { color: var(--primary-text-color); }
        .bar { height: 8px; border-radius: 4px; background: var(--bd-track); overflow: hidden; }
        .bar b { display: block; height: 100%; width: 0; background: var(--bd-accent); transition: width 200ms linear; }
        .row input { display: none; width: 100%; margin: 0; accent-color: var(--bd-warn); }
        .manual .row input { display: block; } .manual .row .bar { display: none; }
        .row .txt { color: var(--bd-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .credit { margin-top: 10px; font-size: 11px; color: var(--bd-muted); text-align: right; }
        .credit a { color: inherit; }
        .err { color: var(--error-color, #f44336); font-size: 13px; margin: 0 0 10px; }
        .tl { margin: 0 0 14px; }
        .tlhead { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; margin-bottom: 4px; }
        .tlhead #tlsrc { color: var(--bd-muted); font-size: 12px; }
        .tl canvas { width: 100%; height: 56px; display: block; border-radius: 6px; background: var(--bd-track); cursor: zoom-in; }
        .tlkey { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--bd-muted); margin-top: 4px; }
        .tlkey i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
        @media (max-width: 420px) { .row { grid-template-columns: 62px 26px 1fr 28px; } .row .txt { display: none; } }
      </style>
      <ha-card>
        <div class="head">
          <div class="title" id="title">Live DMX</div>
          <span class="chip" id="state">…</span>
          <button id="manual" title="Set DMX values by hand, for testing and calibrating">Manual DMX</button>
          <button id="strobe" title="Strobe on top of the program: the heads keep moving">Strobe</button>
          <button id="blackout" title="Every DMX channel of every head to 0">Blackout</button>
          <div class="beats" id="beats"><i></i><i></i><i></i><i></i></div>
          <div class="tempo"><span class="bpm" id="bpm">–</span><span class="unit">BPM</span></div>
        </div>
        <dl class="info">
          <dt>Tempo</dt><dd id="found" class="found"></dd>
          <dt>Playing</dt><dd id="track"></dd>
          <dt>Show</dt><dd id="show"></dd>
        </dl>
        <div class="tl" id="tl" hidden>
          <div class="tlhead"><span id="tlnow"></span><span id="tlsrc"></span></div>
          <canvas id="tlc" height="56" title="Click for a larger view"></canvas>
          <div class="tlkey" id="tlkey"></div>
        </div>
        <div class="err" id="err" hidden></div>
        <div class="fixtures" id="fixtures"></div>
        <div class="credit" id="credit" hidden>Tempo data provided by
          <a href="https://getsongbpm.com" target="_blank" rel="noopener">GetSongBPM.com</a></div>
      </ha-card>`;
    const $ = (id) => root.getElementById(id);
    $("strobe").addEventListener("click", () => this._safe(() => {
      const entity_id = this._config.strobe_entity || "switch.beat_dmx_strobe";
      this._hass.callService("switch", this._d && this._d.strobe ? "turn_off" : "turn_on", { entity_id }).catch((e) => this._toast(e));
    }));
    $("blackout").addEventListener("click", () => this._safe(() => {
      const entity_id = this._config.blackout_entity || "switch.beat_dmx_blackout";
      this._hass.callService("switch", this._d && this._d.blackout ? "turn_off" : "turn_on", { entity_id }).catch((e) => this._toast(e));
    }));
    this._els = { card: root.querySelector("ha-card"), state: $("state"), manual: $("manual"), bpm: $("bpm"),
      found: $("found"), track: $("track"), show: $("show"), beats: [...$("beats").children],
      fixtures: $("fixtures"), credit: $("credit"), err: $("err"),
      tl: $("tl"), tlnow: $("tlnow"), tlsrc: $("tlsrc"), tlc: $("tlc"), tlkey: $("tlkey") };
    $("tlc").addEventListener("click", () => this._safe(() => this._openBig()));
    $("tlkey").innerHTML = Object.entries(SECTION_COLOURS).filter(([k]) => k !== "Outro")
      .map(([k, c]) => `<span><i style="background:${c}"></i>${k === "Intro" ? "Intro/outro" : k}</span>`).join("") +
      '<span><i style="background:#fff"></i>Peak</span><span><i style="background:#000;outline:1px solid #666"></i>Quiet moment</span>';
    this._els.manual.addEventListener("click", () => this._safe(() => this._toggleManual()));
    root.getElementById("title").textContent = this._config.title || "Live DMX";
    this._fx = [];
  }

  _render(d) {
    if (!this._els) return;
    const e = this._els;
    if (d.error) {
      e.state.textContent = "Unavailable";
      e.track.innerHTML = `<span class="err">${esc(d.error)}</span>`;
      return;
    }
    this._d = d;
    if (Array.isArray(d.colours) && d.colours.length === 8) WHEEL = d.colours;
    e.card.classList.toggle("manual", !!d.manual);
    e.manual.classList.toggle("active", !!d.manual);
    e.state.textContent = d.blackout ? "Blackout" : d.dark ? "Dark – no music" : d.manual ? "Manual DMX" : d.active ? "Driving" : d.enabled ? "Waiting for music" : "Beat sync off";
    e.state.className = `chip ${d.dark ? "dark" : d.manual ? "manual" : d.active ? "on" : ""}`;
    this.shadowRoot.getElementById("blackout").classList.toggle("active", !!d.blackout);
    this.shadowRoot.getElementById("strobe").classList.toggle("active", !!d.strobe);
    e.bpm.textContent = d.bpm?.toFixed(1) ?? "–";
    // the beat comes about 5x a second; in between the card counts on from it
    this._beat0 = d.beat;
    this._beatAt = performance.now();
    if (d.beat != null && !this._beatTimer) this._beatTimer = setInterval(() => this._safe(() => this._tickBeat()), 50);
    if (d.beat == null && this._beatTimer) { clearInterval(this._beatTimer); this._beatTimer = null; }
    this._tickBeat();

    const tempo = tempoStatus(d.source);
    e.found.textContent = tempo.text;
    e.found.className = `found ${tempo.cls}`;
    e.found.title = `source: ${d.source}`;
    e.track.textContent = d.title ? `${d.artist ? `${d.artist} – ` : ""}${d.title}${d.player ? ` · ${d.player}` : ""}` : "Nothing playing";
    e.track.title = e.track.textContent;
    const now = (setting, playing) => (setting === "Random mix" && playing ? `Random mix → ${playing}` : setting);
    e.show.textContent = `${d.program} · ${d.shape} · ${now(d.coordination, d.coordination_now)}` +
      (d.colour_mode ? ` · colours ${now(d.colour_mode, d.colour_now)}` : "") +
      (d.strobe ? ` · STROBE (${d.strobe_colour === "Program colour" ? "program colour" : d.strobe_colour})` : "") +
      (d.zone ? ` · zone ±${Math.round(d.zone.width)}°, ${Math.round(d.zone.low)}–${Math.round(d.zone.high)}° up` : "");
    e.credit.hidden = !String(d.source || "").startsWith("getsongbpm");
    this._renderSection(d);

    const fixtures = this._ordered(d.fixtures);
    const sig = fixtures.map((f) => f.prefix).join();
    if (sig !== this._sig) {
      this._sig = sig;
      this._buildFixtures(fixtures);
    }
    fixtures.forEach((f, i) => this._updateFixture(this._fx[i], f, d.manual));
  }

  // ---- section timeline ------------------------------------------------------------------
  _renderSection(d) {
    const e = this._els, sec = d.section;
    e.tl.hidden = !sec;
    if (!sec) return;
    // the curve and sections are fetched once per track (and again when MA's analysis arrives)
    const tlKey = `${sec.id}|${sec.source}`;
    if (tlKey !== this._tlKey) {
      this._tlKey = tlKey;
      this._tl = null;
      if (this._big) this._big.close(); // (a new track: its own timeline)
      this._hass.connection.sendMessagePromise({ type: "beat_dmx/timeline" })
        .then((tl) => this._safe(() => { if (this._tlKey === tlKey) { this._tl = tl; this._drawTimeline(); } }))
        .catch((err) => this._toast(err));
    }
    this._sec = sec;
    this._secAt = performance.now();
    if (!sec.source) {
      e.tlnow.textContent = "Not analysed yet – the normal show runs";
      e.tlsrc.textContent = "";
    } else {
      e.tlnow.textContent = `${d.section_show ? "" : "(Section show off) "}Section: ${sec.type || "–"}${sec.peak ? " · PEAK" : ""}${sec.quiet ? " · QUIET" : ""}` +
        (sec.next ? ` · ${sec.next} in ${Math.max(0, Math.round(sec.next_in))} s` : "");
      e.tlsrc.textContent = sec.source === "file" ? "" : "from Music Assistant's analysis";
    }
    if (!this._tlTimer) this._tlTimer = setInterval(() => this._safe(() => this._drawTimeline()), 250);
  }

  _drawTimeline() {
    if (!this._els) return;
    this._drawOn(this._els.tlc, 56, false);
    if (this._big) this._drawOn(this._big.canvas, 280, true);
  }

  // the timeline on a canvas: section bands, the energy curve, peaks (white), quiet moments (black),
  // the playhead; `big` adds a time axis and the section names
  _drawOn(c, h, big) {
    const tl = this._tl;
    if (!c || !tl || !tl.duration) return;
    const w = Math.max(100, Math.round(c.clientWidth || 600)), dpr = window.devicePixelRatio || 1;
    if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
    const g = c.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const X = (t) => (t / tl.duration) * w;
    (tl.sections || []).forEach((s) => {
      g.fillStyle = SECTION_COLOURS[s.type] || "#888";
      g.globalAlpha = 0.45;
      g.fillRect(X(s.start), 0, Math.max(1, X(s.end) - X(s.start)), h);
    });
    g.globalAlpha = 1;
    const curve = tl.timeline || [];
    if (curve.length) {
      g.beginPath();
      curve.forEach((v, i) => { const x = (i / curve.length) * w, y = h - (Math.min(v, 255) / 255) * (h - 4); i ? g.lineTo(x, y) : g.moveTo(x, y); });
      g.lineTo(w, h); g.lineTo(0, h); g.closePath();
      g.fillStyle = "rgba(255,255,255,0.35)";
      g.fill();
    }
    g.fillStyle = "#fff";
    (tl.peaks || []).forEach(([a]) => g.fillRect(X(a), 0, 2, 6));
    g.fillStyle = "#000";
    (tl.quiet || []).forEach(([a, b]) => g.fillRect(X(a), h - 8, Math.max(2, X(b) - X(a)), 8));
    g.fillStyle = "#fff";
    if (big) {
      g.font = "12px sans-serif";
      g.textBaseline = "top";
      (tl.sections || []).forEach((s) => {
        const x0 = X(s.start), x1 = X(s.end);
        if (x1 - x0 < 46) return;
        g.fillStyle = "rgba(0,0,0,0.55)";
        g.fillRect(x0 + 3, 4, Math.min(x1 - x0 - 6, g.measureText(s.type).width + 8), 17);
        g.fillStyle = "#fff";
        g.fillText(s.type, x0 + 7, 7);
      });
      const step = tl.duration > 1200 ? 300 : tl.duration > 400 ? 60 : 30;
      g.fillStyle = "rgba(255,255,255,0.8)";
      g.textBaseline = "bottom";
      for (let t = step; t < tl.duration; t += step) {
        g.fillRect(X(t), h - 6, 1, 6);
        g.fillText(mmss(t), X(t) + 3, h - 1);
      }
    }
    const sec = this._sec;
    if (sec && sec.t != null) {
      const t = sec.t + (performance.now() - this._secAt) / 1000;
      g.fillStyle = "#fff";
      g.fillRect(Math.min(w - 2, X(t)), 0, 2, h);
      if (!big) this._els.tlsrc.title = `${mmss(t)} / ${mmss(tl.duration)}`;
      else this._big.now.textContent = `${mmss(t)} / ${mmss(tl.duration)}`;
    }
  }

  // the large view: a window over the page (on document.body, so nothing in the dashboard clips it)
  _openBig() {
    if (!this._tl || this._big) return;
    const d = this._d || {}, tl = this._tl, bg = "#16181d";
    const ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;padding:16px;";
    const sections = (tl.sections || []).map((s) => `<tr><td>${mmss(s.start)}–${mmss(s.end)}</td><td><i style="display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:${SECTION_COLOURS[s.type] || "#888"}"></i>${esc(s.type)}</td></tr>`).join("");
    ov.innerHTML = `
      <div style="background:${bg};color:#eee;border-radius:14px;width:min(1200px,96vw);max-height:92vh;overflow:auto;padding:18px 20px;font:14px/1.4 var(--paper-font-body1_-_font-family, sans-serif);box-shadow:0 10px 40px rgba(0,0,0,.6)">
        <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
          <div style="font-size:18px;font-weight:500;flex:1">${esc(d.title ? `${d.artist ? d.artist + " – " : ""}${d.title}` : "Track")}</div>
          <div style="color:#aaa">${tl.bpm ? `${tl.bpm} BPM · ` : ""}<span data-now></span></div>
          <button data-close style="font:inherit;background:none;border:1px solid #555;color:#eee;border-radius:14px;padding:3px 12px;cursor:pointer">✕ Close</button>
        </div>
        <canvas data-c style="width:100%;height:280px;display:block;border-radius:8px;background:#23262d;margin:12px 0 6px"></canvas>
        <div style="display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#aaa;margin-bottom:12px">${this._els.tlkey.innerHTML}</div>
        <table style="border-collapse:collapse;font-size:13px">${sections}</table>
        <div style="color:#888;font-size:12px;margin-top:8px">${(tl.peaks || []).length} peaks · ${(tl.quiet || []).length} quiet moments</div>
      </div>`;
    ov.querySelectorAll("td").forEach((td) => (td.style.padding = "2px 14px 2px 0"));
    const close = () => { ov.remove(); this._big = null; document.removeEventListener("keydown", onKey); };
    const onKey = (ev) => { if (ev.key === "Escape") close(); };
    ov.addEventListener("click", (ev) => { if (ev.target === ov) close(); });
    ov.querySelector("[data-close]").addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(ov);
    this._big = { el: ov, canvas: ov.querySelector("[data-c]"), now: ov.querySelector("[data-now]"), close };
    this._drawTimeline();
  }

  _tickBeat() {
    const d = this._d;
    if (!this._els || !d) return;
    const now = this._beat0 == null ? null : this._beat0 + ((performance.now() - this._beatAt) / 1000) * (d.bpm / 60);
    const beat = now == null ? -1 : Math.floor(now);
    const onBeat = now != null && now - beat < 0.25;
    this._els.beats.forEach((b, i) => b.classList.toggle("lit", onBeat && ((beat % 4) + 4) % 4 === i));
  }

  _ordered(fixtures) {
    const order = this._config.fixtures || [];
    const rank = (f) => {
      const i = order.findIndex((o) => o === f.prefix || o === f.name);
      return i < 0 ? order.length : i;
    };
    return [...fixtures].sort((a, b) => rank(a) - rank(b));
  }

  _buildFixtures(fixtures) {
    this._els.fixtures.innerHTML = "";
    this._els.fixtures.classList.toggle("stacked", this._config.layout === "stacked");
    this._fx = fixtures.map((f) => {
      const el = document.createElement("div");
      el.className = "fx";
      el.innerHTML = `
        <h3>${esc(f.name)} <span>DMX ${f.start ?? "?"}–${f.start != null ? f.start + 10 : "?"}</span></h3>
        <div class="pad"><div class="dot"></div><div class="padhint">drag to aim: pan ↔ (0–255), tilt ↕ (0–255)</div><div class="padlabel"></div></div>
        <div class="cal">
          <b>Calibrate this head</b> (so the zone and the 3D view know its front)
          <ol>
            <li><button data-point="go_up">Go</button> Adjust <i>Tilt</i> until the beam points <b>straight up</b>, then
              <button data-point="up" class="set">It points straight up</button></li>
            <li><button data-point="go_level">Go</button> Turn <i>Pan</i> until the beam points <b>into the middle of the room</b>
              (away from the wall behind the head) and adjust <i>Tilt</i> until it is <b>level</b>, then
              <button data-point="level" class="set">It points level, into the room</button></li>
            <li><button data-point="go_left">Check</button> The beam should now point <b>45° to the left</b> (as seen from behind the head) and a bit up.
              If it went right: <button data-point="reverse_pan" class="set">Flip pan direction</button></li>
          </ol>
          <div class="caltext"></div>
          <div class="btns"><button data-point="reset">Reset calibration</button></div>
        </div>
        ${CHANNELS.map((c, i) => `
          <div class="row"><span>${c.label}</span><span class="addr">${f.start != null ? f.start + i : ""}</span>
            <div class="bar"><b></b></div><input type="range" min="0" max="255" step="1" data-ch="${i + 1}">
            <span class="val">–</span><span class="txt"></span></div>`).join("")}`;
      this._els.fixtures.appendChild(el);

      el.querySelectorAll(".cal button").forEach((b) => b.addEventListener("click", () => this._safe(() => this._calibrate(f.prefix, b.dataset.point))));
      el.querySelectorAll(".row input").forEach((inp) =>
        inp.addEventListener("input", () => this._safe(() => this._send(f.prefix, Number(inp.dataset.ch), Number(inp.value)))));
      const pad = el.querySelector(".pad");
      const aim = (ev) => {
        if (!this._d || !this._d.manual) return;
        const r = pad.getBoundingClientRect();
        const x = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1), y = Math.min(Math.max((ev.clientY - r.top) / r.height, 0), 1);
        this._send(f.prefix, 1, x * 255);
        this._send(f.prefix, 3, y * 255);
      };
      // (the pad keeps the pointer while dragging; touch scrolling is off on it)
      let dragging = false;
      pad.addEventListener("pointerdown", (ev) => this._safe(() => {
        if (!this._d || !this._d.manual) return;
        dragging = true;
        ev.preventDefault();
        try { pad.setPointerCapture(ev.pointerId); } catch (e) { /* not supported: moves still arrive while over the pad */ }
        aim(ev);
      }));
      pad.addEventListener("pointermove", (ev) => this._safe(() => { if (dragging) aim(ev); }));
      ["pointerup", "pointercancel", "lostpointercapture"].forEach((t) => pad.addEventListener(t, () => (dragging = false)));

      return {
        prefix: f.prefix, dot: el.querySelector(".dot"), padlabel: el.querySelector(".padlabel"), caltext: el.querySelector(".caltext"),
        rows: [...el.querySelectorAll(".row")].map((r) => ({
          bar: r.querySelector(".bar b"), input: r.querySelector("input"), val: r.querySelector(".val"), txt: r.querySelector(".txt") })),
      };
    });
  }

  _updateFixture(ui, f, manual) {
    const v = f.values;
    v.forEach((value, i) => {
      const row = ui.rows[i];
      if (value == null) {
        row.val.textContent = "–";
        row.bar.style.width = "0";
        row.txt.textContent = "";
        return;
      }
      row.val.textContent = value;
      row.bar.style.width = `${(value / 255) * 100}%`;
      if (!manual || !this._held(f.prefix, i + 1)) row.input.value = value;
      row.txt.textContent = CHANNELS[i].decode(value);
    });
    const c = f.calibration;
    if (c) {
      ui.caltext.textContent = `Calibration: straight up = tilt ${c.tilt_up.toFixed(1)} · level into the room = pan ${c.pan_forward.toFixed(1)}, ` +
        `tilt ${c.tilt_level.toFixed(1)} · pan ${c.pan_reverse ? "reversed" : "normal"}`;
    }
    if (v[0] == null) return;
    const shutterOpen = v[6] > 7;
    const level = shutterOpen ? v[7] / 255 : 0;
    ui.dot.style.left = `${(v[0] / 255) * 100}%`;
    ui.dot.style.top = `${(v[2] / 255) * 100}%`;
    ui.dot.style.opacity = String(0.25 + 0.75 * level);
    const col = colourCss(v[4]);
    ui.dot.style.background = col.bg;
    ui.dot.style.boxShadow = level > 0 ? `0 0 ${4 + 16 * level}px ${col.glow}` : "none";
    ui.padlabel.textContent = `${colourName(v[4])} · ${range(v[5], GOBO)}`;
  }
}

// (the file can be loaded twice, e.g. after the app reconnects: define once)
if (!customElements.get("beat-dmx-card")) customElements.define("beat-dmx-card", BeatDmxCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "beat-dmx-card")) window.customCards.push({ type: "beat-dmx-card", name: "Beat DMX live view", description: "Live DMX values of the fixtures Beat DMX controls, with manual DMX for testing" });
