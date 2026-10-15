// Beat DMX live view: the DMX values going out to each fixture Beat DMX controls, pushed ~10x/s
// over the websocket (beat_dmx/subscribe). Channel meanings: BeamZ Panther 40 LED Spot, 11-channel mode.

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
    return 8;
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

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { --bd-accent: var(--primary-color, #03a9f4); --bd-muted: var(--secondary-text-color, #888);
                --bd-track: color-mix(in srgb, var(--primary-text-color, #fff) 10%, transparent); }
        ha-card { padding: 16px; }
        .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .title { font-size: 18px; font-weight: 500; flex: 1; min-width: 120px; }
        .chip { font-size: 12px; padding: 2px 10px; border-radius: 12px; background: var(--bd-track); color: var(--bd-muted); }
        .chip.on { background: color-mix(in srgb, var(--success-color, #4caf50) 22%, transparent); color: var(--success-color, #4caf50); }
        .tempo { display: flex; align-items: baseline; gap: 6px; }
        .bpm { font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
        .unit, .src, .track { color: var(--bd-muted); font-size: 12px; }
        .beats { display: flex; gap: 6px; }
        .beats i { width: 12px; height: 12px; border-radius: 50%; background: var(--bd-track); transition: background 60ms; }
        .beats i.lit { background: var(--bd-accent); box-shadow: 0 0 8px var(--bd-accent); }
        .track { margin: 6px 0 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .fixtures { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
        .fx { border: 1px solid var(--divider-color, #333); border-radius: 12px; padding: 12px; }
        .fx h3 { margin: 0 0 10px; font-size: 15px; font-weight: 500; display: flex; gap: 8px; align-items: center; }
        .fx h3 span { color: var(--bd-muted); font-size: 12px; font-weight: 400; }
        .pad { position: relative; height: 90px; border-radius: 8px; background: var(--bd-track); margin-bottom: 10px; overflow: hidden; }
        .pad::before, .pad::after { content: ""; position: absolute; background: color-mix(in srgb, var(--bd-muted) 30%, transparent); }
        .pad::before { left: 50%; top: 0; bottom: 0; width: 1px; } .pad::after { top: 50%; left: 0; right: 0; height: 1px; }
        .dot { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
               background: #fff; transition: left 100ms linear, top 100ms linear, opacity 100ms linear, box-shadow 100ms linear; }
        .padlabel { position: absolute; right: 8px; bottom: 4px; font-size: 11px; color: var(--bd-muted); }
        .row { display: grid; grid-template-columns: 74px 30px 1fr 30px minmax(0, 104px); gap: 8px; align-items: center;
               font-size: 12px; height: 22px; }
        .row .addr, .row .val { color: var(--bd-muted); font-variant-numeric: tabular-nums; text-align: right; }
        .row .val { color: var(--primary-text-color); }
        .bar { height: 8px; border-radius: 4px; background: var(--bd-track); overflow: hidden; }
        .bar b { display: block; height: 100%; width: 0; background: var(--bd-accent); transition: width 100ms linear; }
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
          <div class="beats" id="beats"><i></i><i></i><i></i><i></i></div>
          <div class="tempo"><span class="bpm" id="bpm">–</span><span class="unit">BPM</span></div>
        </div>
        <div class="track" id="track"></div>
        <div class="fixtures" id="fixtures"></div>
        <div class="credit" id="credit" hidden>Tempo data provided by
          <a href="https://getsongbpm.com" target="_blank" rel="noopener">GetSongBPM.com</a></div>
      </ha-card>`;
    this._els = {
      state: root.getElementById("state"), bpm: root.getElementById("bpm"), track: root.getElementById("track"),
      beats: [...root.getElementById("beats").children], fixtures: root.getElementById("fixtures"),
      credit: root.getElementById("credit"),
    };
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
    e.state.textContent = d.active ? `Driving · ${d.program}` : d.enabled ? "Waiting for music" : "Beat sync off";
    e.state.classList.toggle("on", !!d.active);
    e.bpm.textContent = d.bpm?.toFixed(1) ?? "–";
    const beat = d.beat == null ? -1 : Math.floor(d.beat);
    const onBeat = d.beat != null && d.beat - beat < 0.25;
    e.beats.forEach((b, i) => b.classList.toggle("lit", onBeat && ((beat % 4) + 4) % 4 === i));
    const song = d.title ? `${d.artist ? `${d.artist} – ` : ""}${d.title}` : "Nothing playing";
    e.credit.hidden = !String(d.source || "").startsWith("getsongbpm");
    e.track.textContent = `${song}${d.player ? ` · ${d.player}` : ""} · tempo: ${d.source}`;

    if (this._fx.length !== d.fixtures.length) this._buildFixtures(d.fixtures);
    d.fixtures.forEach((f, i) => this._updateFixture(this._fx[i], f));
  }

  _buildFixtures(fixtures) {
    this._els.fixtures.innerHTML = "";
    this._fx = fixtures.map((f) => {
      const el = document.createElement("div");
      el.className = "fx";
      const start = f.start ?? "?";
      el.innerHTML = `
        <h3>${esc(f.name)} <span>DMX ${start}–${f.start != null ? f.start + 10 : "?"}${f.mirrored ? " · mirrored" : ""}</span></h3>
        <div class="pad"><div class="dot"></div><div class="padlabel"></div></div>
        ${CHANNELS.map((c, i) => `
          <div class="row"><span>${c.label}</span><span class="addr">${f.start != null ? f.start + i : ""}</span>
            <div class="bar"><b></b></div><span class="val">–</span><span class="txt"></span></div>`).join("")}`;
      this._els.fixtures.appendChild(el);
      return {
        dot: el.querySelector(".dot"), padlabel: el.querySelector(".padlabel"),
        rows: [...el.querySelectorAll(".row")].map((r) => ({
          bar: r.querySelector(".bar b"), val: r.querySelector(".val"), txt: r.querySelector(".txt") })),
      };
    });
  }

  _updateFixture(ui, f) {
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
      row.txt.textContent = CHANNELS[i].decode(value);
    });
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
window.customCards.push({ type: "beat-dmx-card", name: "Beat DMX live view", description: "Live DMX values of the fixtures Beat DMX controls" });
