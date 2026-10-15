// Beat DMX live view: the DMX values going out to each fixture Beat DMX controls, pushed ~10x/s
// over the websocket (beat_dmx/subscribe). Channel meanings: BeamZ Panther 40 LED Spot, 11-channel mode.
// Options: fixtures: [prefix, ...] shows those fixtures in that order (others after them);
//          layout: stacked puts the fixtures under each other instead of side by side;
//          manual_entity: the Manual DMX switch (switch.beat_dmx_manual_dmx by default).
// With Manual DMX on, the bars become sliders and the pan/tilt pad can be dragged (beat_dmx.set_channel),
// and each head can be calibrated from where it points (beat_dmx.calibrate).

const range = (v, table) => {
  for (const [hi, text] of table) if (v <= hi) return typeof text === "function" ? text(v) : text;
  return "";
};
const pct = (v, lo = 0, hi = 255) => `${Math.round(((v - lo) / (hi - lo)) * 100)}%`;
const COLOUR = [
  [7, "White"], [14, "Colour 1"], [21, "Colour 2"], [28, "Colour 3"], [35, "Colour 4"], [42, "Colour 5"],
  [49, "Colour 6"], [56, "Colour 7"], [66, "White + C1"], [76, "C1 + C2"], [86, "C2 + C3"], [96, "C3 + C4"],
  [106, "C4 + C5"], [116, "C5 + C6"], [127, "C6 + C7"],
  [189, (v) => `Rotate ${pct(189 - v, 0, 61)} fast`], [193, "Rotation stop"], [255, (v) => `Rotate ${pct(v, 194, 255)} fast`],
];
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
  { label: "Colour", decode: (v) => range(v, COLOUR) },
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

const SEND_MS = 80; // manual DMX: at most one service call per channel this often while dragging
const HOLD_MS = 600; // and the slider is left alone for this long after it was moved

class BeatDmxCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._unsub && !this._subscribing && this.isConnected) this._subscribe();
  }

  connectedCallback() {
    if (!this.shadowRoot) this._build();
    if (this._hass && !this._unsub && !this._subscribing) this._subscribe();
  }

  disconnectedCallback() {
    if (this._unsub) this._unsub.then((u) => u()).catch(() => {});
    this._unsub = null;
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
      .subscribeMessage((data) => this._render(data), { type: "beat_dmx/subscribe" })
      .catch((err) => {
        this._unsub = null;
        this._render({ error: err.message || "Beat DMX is not available" });
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
    const calls = [...this._pending.values()];
    this._pending.clear();
    calls.forEach((data) => this._hass.callService("beat_dmx", "set_channel", data).catch((e) => this._toast(e)));
  }

  _held(fixture, channel) {
    return Date.now() - (this._touched.get(`${fixture}/${channel}`) || 0) < HOLD_MS;
  }

  _toast(err) {
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
               background: #fff; transition: left 100ms linear, top 100ms linear, opacity 100ms linear, box-shadow 100ms linear; }
        .padlabel { position: absolute; right: 8px; bottom: 4px; font-size: 11px; color: var(--bd-muted); pointer-events: none; }
        .padhint { position: absolute; left: 8px; top: 4px; font-size: 11px; color: var(--bd-warn); display: none; pointer-events: none; }
        .manual .padhint { display: block; }
        .cal { display: none; margin: -2px 0 10px; font-size: 12px; color: var(--bd-muted); }
        .manual .cal { display: block; }
        .cal .btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
        .row { display: grid; grid-template-columns: 74px 30px 1fr 30px minmax(0, 104px); gap: 8px; align-items: center;
               font-size: 12px; height: 24px; }
        .row .addr, .row .val { color: var(--bd-muted); font-variant-numeric: tabular-nums; text-align: right; }
        .row .val { color: var(--primary-text-color); }
        .bar { height: 8px; border-radius: 4px; background: var(--bd-track); overflow: hidden; }
        .bar b { display: block; height: 100%; width: 0; background: var(--bd-accent); transition: width 100ms linear; }
        .row input { display: none; width: 100%; margin: 0; accent-color: var(--bd-warn); }
        .manual .row input { display: block; } .manual .row .bar { display: none; }
        .row .txt { color: var(--bd-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .credit { margin-top: 10px; font-size: 11px; color: var(--bd-muted); text-align: right; }
        .credit a { color: inherit; }
        .err { color: var(--error-color, #f44336); }
        @media (max-width: 420px) { .row { grid-template-columns: 62px 26px 1fr 28px; } .row .txt { display: none; } }
      </style>
      <ha-card>
        <div class="head">
          <div class="title">Live DMX</div>
          <span class="chip" id="state">…</span>
          <button id="manual" title="Set DMX values by hand, for testing and calibrating">Manual DMX</button>
          <div class="beats" id="beats"><i></i><i></i><i></i><i></i></div>
          <div class="tempo"><span class="bpm" id="bpm">–</span><span class="unit">BPM</span></div>
        </div>
        <dl class="info">
          <dt>Tempo</dt><dd id="found" class="found"></dd>
          <dt>Playing</dt><dd id="track"></dd>
          <dt>Show</dt><dd id="show"></dd>
        </dl>
        <div class="fixtures" id="fixtures"></div>
        <div class="credit" id="credit" hidden>Tempo data provided by
          <a href="https://getsongbpm.com" target="_blank" rel="noopener">GetSongBPM.com</a></div>
      </ha-card>`;
    const $ = (id) => root.getElementById(id);
    this._els = { card: root.querySelector("ha-card"), state: $("state"), manual: $("manual"), bpm: $("bpm"),
      found: $("found"), track: $("track"), show: $("show"), beats: [...$("beats").children],
      fixtures: $("fixtures"), credit: $("credit") };
    this._els.manual.addEventListener("click", () => this._toggleManual());
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
    e.card.classList.toggle("manual", !!d.manual);
    e.manual.classList.toggle("active", !!d.manual);
    e.state.textContent = d.manual ? "Manual DMX" : d.active ? "Driving" : d.enabled ? "Waiting for music" : "Beat sync off";
    e.state.className = `chip ${d.manual ? "manual" : d.active ? "on" : ""}`;
    e.bpm.textContent = d.bpm?.toFixed(1) ?? "–";
    const beat = d.beat == null ? -1 : Math.floor(d.beat);
    const onBeat = d.beat != null && d.beat - beat < 0.25;
    e.beats.forEach((b, i) => b.classList.toggle("lit", onBeat && ((beat % 4) + 4) % 4 === i));

    const tempo = tempoStatus(d.source);
    e.found.textContent = tempo.text;
    e.found.className = `found ${tempo.cls}`;
    e.found.title = `source: ${d.source}`;
    e.track.textContent = d.title ? `${d.artist ? `${d.artist} – ` : ""}${d.title}${d.player ? ` · ${d.player}` : ""}` : "Nothing playing";
    e.track.title = e.track.textContent;
    e.show.textContent = `${d.program} · ${d.shape} · ${d.coordination}` +
      (d.zone ? ` · zone ±${Math.round(d.zone.width)}°, ${Math.round(d.zone.low)}–${Math.round(d.zone.high)}° up` : "");
    e.credit.hidden = !String(d.source || "").startsWith("getsongbpm");

    const fixtures = this._ordered(d.fixtures);
    const sig = fixtures.map((f) => f.prefix).join();
    if (sig !== this._sig) {
      this._sig = sig;
      this._buildFixtures(fixtures);
    }
    fixtures.forEach((f, i) => this._updateFixture(this._fx[i], f, d.manual));
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
        <div class="pad"><div class="dot"></div><div class="padhint">drag to aim (pan ↔, tilt ↕)</div><div class="padlabel"></div></div>
        <div class="cal">
          <div class="caltext"></div>
          <div class="btns">
            <button data-point="up" title="Aim the head straight up with pan/tilt, then press">It points straight up</button>
            <button data-point="level" title="Aim the head level, into the middle of the room, then press">It points level, into the room</button>
            <button data-point="reverse_pan" title="Press if the head turns the wrong way in the 3D view">Flip pan direction</button>
            <button data-point="reset">Reset</button>
          </div>
        </div>
        ${CHANNELS.map((c, i) => `
          <div class="row"><span>${c.label}</span><span class="addr">${f.start != null ? f.start + i : ""}</span>
            <div class="bar"><b></b></div><input type="range" min="0" max="255" step="1" data-ch="${i + 1}">
            <span class="val">–</span><span class="txt"></span></div>`).join("")}`;
      this._els.fixtures.appendChild(el);

      el.querySelectorAll(".cal button").forEach((b) => b.addEventListener("click", () => this._calibrate(f.prefix, b.dataset.point)));
      el.querySelectorAll(".row input").forEach((inp) =>
        inp.addEventListener("input", () => this._send(f.prefix, Number(inp.dataset.ch), Number(inp.value))));
      const pad = el.querySelector(".pad");
      const aim = (ev) => {
        if (!this._d || !this._d.manual) return;
        const r = pad.getBoundingClientRect();
        const x = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1), y = Math.min(Math.max((ev.clientY - r.top) / r.height, 0), 1);
        this._send(f.prefix, 1, x * 255);
        this._send(f.prefix, 3, y * 255);
      };
      pad.addEventListener("pointerdown", (ev) => { if (this._d && this._d.manual) { pad.setPointerCapture(ev.pointerId); aim(ev); } });
      pad.addEventListener("pointermove", (ev) => { if (pad.hasPointerCapture(ev.pointerId)) aim(ev); });

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
    ui.dot.style.boxShadow = level > 0 ? `0 0 ${4 + 16 * level}px rgba(255,255,255,${level})` : "none";
    ui.padlabel.textContent = `${range(v[4], COLOUR)} · ${range(v[5], GOBO)}`;
  }
}

customElements.define("beat-dmx-card", BeatDmxCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "beat-dmx-card", name: "Beat DMX live view", description: "Live DMX values of the fixtures Beat DMX controls, with manual DMX for testing" });
