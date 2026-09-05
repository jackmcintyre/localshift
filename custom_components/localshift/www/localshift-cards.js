/**
 * LocalShift Cards — the one Lovelace card core Home Assistant cannot provide.
 *
 * Card:
 *   custom:localshift-plan-timeline — the fused forward plan: price bands,
 *                                     peak window, planned actions, and SOC
 *                                     (actual behind now, planned ahead of it)
 *
 * Everything else the dashboard shows is built from native cards. `history-graph`
 * and `statistics-graph` only plot the past, so a forward-looking series is the
 * single thing left with no native equivalent — this file exists for that alone.
 * The status headline, live power flows, decision feed and cost history that
 * earlier versions of this file rendered are now heading/tile/markdown/
 * statistics-graph cards in dashboard.yaml.
 *
 * Install (no HACS needed):
 *   1. Copy this file to <ha-config>/www/localshift/localshift-cards.js
 *   2. Settings → Dashboards → Resources → Add:
 *        URL:  /local/localshift/localshift-cards.js?v=2.0.0
 *        Type: JavaScript module
 *      (bump the ?v= query when updating, or browsers keep the old module)
 *
 * All entity ids are configurable:
 *   type: custom:localshift-plan-timeline
 *   entities:
 *     soc: sensor.my_home_percentage_charged   # Tesla/Teslemetry
 *     plan: sensor.localshift_optimizer_plan_detailed
 *   dw_start: "15:00"   # demand window (peak) start, local time
 *   dw_end: "21:00"
 *
 * Note: deploy.sh copies this file with the integration, but HA only serves
 * /local/ from <ha-config>/www — copy it there (step 1) when updating.
 */

const LS_VERSION = "2.0.0";

/** Default to full section width in the sections-view grid. */
const LS_FULL_WIDTH = {
  getLayoutOptions() {
    return { grid_columns: "full", grid_rows: "auto" };
  },
  getGridOptions() {
    return { columns: "full", rows: "auto" };
  },
};

const LS_ENTITIES = {
  soc: "sensor.my_home_percentage_charged",
  cheap_price: "sensor.localshift_price_cheap_effective",
  plan: "sensor.localshift_optimizer_plan_detailed",
  summary: "sensor.localshift_optimizer_summary",
  target: "number.localshift_battery_target",
};

const LS_ACTION_META = {
  charge_grid_normal: { color: "#34d399", label: "Charge" },
  charge_grid_boost: { color: "#fbbf24", label: "Boost" },
  proactive_export: { color: "#a78bfa", label: "Export" },
  spike_discharge: { color: "#f87171", label: "Sell" },
  hold: { color: "rgba(148,163,184,0.22)", label: "Hold" },
};

/* ---------------------------------------------------------------- helpers */

function lsState(hass, id) {
  const s = hass.states[id];
  return s ? s.state : undefined;
}
function lsNum(hass, id, fallback = 0) {
  const v = parseFloat(lsState(hass, id));
  return Number.isFinite(v) ? v : fallback;
}
function lsAttr(hass, id, attr, fallback = undefined) {
  const s = hass.states[id];
  return s && s.attributes && s.attributes[attr] !== undefined
    ? s.attributes[attr]
    : fallback;
}
function lsEsc(str) {
  return String(str ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}
function lsHHMM(d) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
function lsParseHHMM(str, base) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(str).trim());
  const d = new Date(base);
  d.setHours(m ? +m[1] : 15, m ? +m[2] : 0, 0, 0);
  return d;
}

/** REST history fetch with a small cache. Returns [{t, v}, ...] sorted by t. */
async function lsFetchHistory(hass, cache, key, entityId, startMs, endMs, ttlMs) {
  const now = Date.now();
  const hit = cache[key];
  if (hit && now - hit.at < ttlMs) return hit.data;
  // The pending lock must expire: a request that never settles (e.g. a tab
  // left open across an HA restart) would otherwise freeze history forever.
  if (hit && hit.pending && now - hit.pendingAt < 30e3) return hit.data || [];
  const mine = { at: hit ? hit.at : 0, data: hit ? hit.data : [], pending: true, pendingAt: now };
  cache[key] = mine;
  try {
    const url =
      `history/period/${new Date(startMs).toISOString()}` +
      `?filter_entity_id=${entityId}` +
      `&end_time=${encodeURIComponent(new Date(endMs).toISOString())}` +
      `&minimal_response&no_attributes`;
    const res = await hass.callApi("GET", url);
    const rows = (res && res[0]) || [];
    const data = rows
      .map((r) => ({ t: new Date(r.last_changed || r.last_updated).getTime(), v: parseFloat(r.state) }))
      .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
      .sort((a, b) => a.t - b.t);
    if (cache[key] === mine) cache[key] = { at: Date.now(), data, pending: false };
    return data;
  } catch (e) {
    if (cache[key] === mine) cache[key] = { at: Date.now(), data: (hit && hit.data) || [], pending: false };
    return (hit && hit.data) || [];
  }
}

const LS_BASE_CSS = `
  :host { display: block; }
  ha-card { padding: 16px; overflow: hidden; }
  .ls-muted { color: var(--secondary-text-color); }
  .ls-setup {
    margin-top: 12px; padding: 8px 10px; border-radius: 8px; font-size: 12px;
    background: rgba(251,191,36,0.10); color: var(--secondary-text-color);
  }
  .ls-setup code { font-size: 11px; }
`;

function lsMissingEntities(hass, ids) {
  return ids.filter((id) => id && !hass.states[id]);
}
function lsSetupHint(missing) {
  if (!missing.length) return "";
  return `<div class="ls-setup">⚠ Missing entities (set them in the card's <code>entities:</code> config): ${missing
    .map((m) => `<code>${lsEsc(m)}</code>`)
    .join(", ")}</div>`;
}

/* ======================================================= plan timeline === */

class LocalShiftPlanTimelineCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._histCache = {};
    this._width = 0;
  }

  setConfig(config) {
    this._e = { ...LS_ENTITIES, ...(config.entities || {}) };
    this._cfg = {
      title: "Plan",
      dw_start: "15:00",
      dw_end: "21:00",
      price_high: 0.3, // $/kWh — amber tint above this
      price_spike: 0.6, // $/kWh — red tint above this
      hours_past: 6,
      hours_future: 24,
      ...config,
    };
    this._snapshot = "";
    if (this._hass) this._render();
  }

  connectedCallback() {
    if (!this._ro) {
      this._ro = new ResizeObserver(() => {
        const w = this.shadowRoot.host.offsetWidth || 0;
        if (Math.abs(w - this._width) > 24) {
          this._width = w;
          if (this._hass) this._render();
        }
      });
      this._ro.observe(this);
    }
  }
  disconnectedCallback() {
    if (this._ro) {
      this._ro.disconnect();
      this._ro = null;
    }
  }

  set hass(hass) {
    this._hass = hass;
    const e = this._e;
    if (!e) return;
    const watch = [e.plan, e.soc, e.cheap_price, e.target, e.summary];
    const snap = watch
      .map((id) => {
        const s = hass.states[id];
        return s ? `${id}=${s.state}@${s.last_updated}` : `${id}=∅`;
      })
      .join("|");
    if (snap !== this._snapshot) {
      this._snapshot = snap;
      this._render();
    }
  }

  getCardSize() {
    return 6;
  }

  /* ---- data assembly ---- */

  _decisions() {
    const raw = lsAttr(this._hass, this._e.plan, "decisions", []) || [];
    return raw
      .map((d) => ({ ...d, t: new Date(d.timestamp_iso).getTime() }))
      .filter((d) => Number.isFinite(d.t));
  }

  _dwIntervals(t0, t1) {
    const out = [];
    for (let dayOff = -1; dayOff <= 1; dayOff++) {
      const base = new Date();
      base.setDate(base.getDate() + dayOff);
      const s = lsParseHHMM(this._cfg.dw_start, base).getTime();
      const en = lsParseHHMM(this._cfg.dw_end, base).getTime();
      const a = Math.max(s, t0);
      const b = Math.min(en, t1);
      if (b > a) out.push([a, b]);
    }
    return out;
  }

  _timelineSvg(decisions, socHist, width) {
    const W = Math.max(320, width - 8);
    const H = 224;
    const padL = 6, padR = 6;
    const plotTop = 14, plotBot = 152; // SOC area
    const laneTop = 166, laneBot = 184; // action lane
    const labelY = 206;
    const now = Date.now();
    const t0 = now - this._cfg.hours_past * 3600e3;
    const t1 = now + this._cfg.hours_future * 3600e3;
    const x = (t) => padL + ((t - t0) / (t1 - t0)) * (W - padL - padR);
    const y = (soc) => plotBot - (Math.max(0, Math.min(100, soc)) / 100) * (plotBot - plotTop);

    const hass = this._hass;
    const cheap = lsNum(hass, this._e.cheap_price, NaN);
    const inWin = decisions.filter((d) => d.t >= now - 10 * 60e3 && d.t <= t1);

    let svg = "";

    // past context tint
    svg += `<rect x="${x(t0)}" y="${plotTop}" width="${x(now) - x(t0)}" height="${laneBot - plotTop}" fill="rgba(148,163,184,0.05)"/>`;

    // price tint bands (merge adjacent same-tier slots)
    const tierOf = (p) => {
      if (!Number.isFinite(p)) return 0;
      if (p >= this._cfg.price_spike) return 3;
      if (p >= this._cfg.price_high) return 2;
      if (Number.isFinite(cheap) && p <= cheap) return 1;
      return 0;
    };
    const tierFill = {
      1: "rgba(52,211,153,0.07)",
      2: "rgba(251,146,60,0.07)",
      3: "rgba(248,113,113,0.12)",
    };
    const tierEdge = {
      1: "rgba(52,211,153,0.55)",
      2: "rgba(251,146,60,0.55)",
      3: "rgba(248,113,113,0.7)",
    };
    let i = 0;
    while (i < inWin.length) {
      const tier = tierOf(inWin[i].buy_price);
      let j = i;
      while (j + 1 < inWin.length && tierOf(inWin[j + 1].buy_price) === tier) j++;
      if (tier > 0) {
        const a = x(inWin[i].t);
        const slotMs = (inWin[i].slot_interval_minutes || 5) * 60e3;
        const b = x(inWin[j].t + slotMs);
        svg += `<rect x="${a}" y="${plotTop}" width="${Math.max(0.5, b - a)}" height="${plotBot - plotTop}" fill="${tierFill[tier]}"/>`;
        svg += `<rect x="${a}" y="${plotBot + 2}" width="${Math.max(0.5, b - a)}" height="2.5" rx="1.25" fill="${tierEdge[tier]}"/>`;
      }
      i = j + 1;
    }

    // demand window (peak) regions
    for (const [a, b] of this._dwIntervals(t0, t1)) {
      svg += `<rect x="${x(a)}" y="${plotTop}" width="${x(b) - x(a)}" height="${laneBot - plotTop}" fill="url(#lsHatch)" stroke="rgba(251,146,60,0.55)" stroke-width="1" rx="3"/>`;
      svg += `<text x="${(x(a) + x(b)) / 2}" y="${plotTop + 12}" text-anchor="middle" class="ls-dw-label">PEAK</text>`;
    }

    // target & floor reference lines
    const target = lsNum(hass, this._e.target, NaN);
    if (Number.isFinite(target)) {
      svg += `<line x1="${padL}" y1="${y(target)}" x2="${W - padR}" y2="${y(target)}" stroke="rgba(255,255,255,0.28)" stroke-dasharray="2 5"/>`;
      svg += `<text x="${W - padR - 2}" y="${y(target) - 4}" text-anchor="end" class="ls-ref-label">target ${Math.round(target)}%</text>`;
    }
    const cfgOpts = lsAttr(hass, this._e.summary, "config_options", {}) || {};
    const floor = parseFloat(cfgOpts.minimum_target_soc);
    if (Number.isFinite(floor)) {
      svg += `<line x1="${padL}" y1="${y(floor)}" x2="${W - padR}" y2="${y(floor)}" stroke="rgba(248,113,113,0.25)" stroke-dasharray="2 5"/>`;
    }

    // action lane
    i = 0;
    while (i < inWin.length) {
      const a = inWin[i].action;
      let j = i;
      while (j + 1 < inWin.length && inWin[j + 1].action === a) j++;
      const meta = LS_ACTION_META[a] || { color: "rgba(148,163,184,0.2)" };
      const slotMs = (inWin[i].slot_interval_minutes || 5) * 60e3;
      svg += `<rect x="${x(inWin[i].t)}" y="${laneTop}" width="${Math.max(1, x(inWin[j].t + slotMs) - x(inWin[i].t))}" height="${laneBot - laneTop}" rx="3" fill="${meta.color}"/>`;
      i = j + 1;
    }

    // actual SOC (history, extended to the live state so the line keeps
    // tracking current SOC even when the history fetch lags or fails)
    const liveSoc = lsNum(hass, this._e.soc, NaN);
    const actual = (socHist || []).filter((p) => p.t >= t0 && p.t <= now);
    if (Number.isFinite(liveSoc)) actual.push({ t: now, v: liveSoc });
    if (actual.length > 1) {
      const pts = actual.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
      svg += `<polyline points="${pts}" fill="none" stroke="var(--primary-text-color)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    }

    // planned SOC (dashed, future)
    if (inWin.length > 1) {
      const pts = inWin.map((d) => `${x(d.t).toFixed(1)},${y(d.predicted_soc_pct).toFixed(1)}`).join(" ");
      svg += `<polyline points="${pts}" fill="none" stroke="#f59e0b" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round"/>`;
    }

    // now marker
    svg += `<line x1="${x(now)}" y1="${plotTop - 4}" x2="${x(now)}" y2="${laneBot}" stroke="#ef4444" stroke-width="1.5"/>`;
    svg += `<text x="${x(now)}" y="${plotTop - 6}" text-anchor="middle" class="ls-now-label">now</text>`;

    // time axis labels — every 6 h, snapped to LOCAL 00/06/12/18
    const cursor = new Date(t0);
    cursor.setMinutes(0, 0, 0);
    while (cursor.getTime() < t0 || cursor.getHours() % 6 !== 0)
      cursor.setHours(cursor.getHours() + 1);
    while (cursor.getTime() <= t1) {
      const t = cursor.getTime();
      const d = new Date(t);
      const lab = d.getHours() === 0
        ? d.toLocaleDateString([], { weekday: "short" })
        : lsHHMM(d);
      svg += `<text x="${x(t)}" y="${labelY}" text-anchor="middle" class="ls-axis-label">${lab}</text>`;
      svg += `<line x1="${x(t)}" y1="${laneBot + 2}" x2="${x(t)}" y2="${laneBot + 7}" stroke="rgba(148,163,184,0.4)"/>`;
      cursor.setHours(cursor.getHours() + 6);
    }

    return `
      <svg width="${W}" height="${H}" style="display:block">
        <defs>
          <pattern id="lsHatch" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(251,146,60,0.22)" stroke-width="3"/>
          </pattern>
        </defs>
        ${svg}
      </svg>`;
  }

  /* ---- render ---- */

  async _ensureHistory() {
    const e = this._e;
    const now = Date.now();
    const data = await lsFetchHistory(
      this._hass, this._histCache, "soc", e.soc,
      now - this._cfg.hours_past * 3600e3, now, 4 * 60e3
    );
    if (data !== this._socHist) {
      this._socHist = data;
      this._renderTimelineOnly();
    }
  }

  _renderTimelineOnly() {
    const holder = this.shadowRoot.querySelector(".ls-timeline");
    if (holder && this._hass) {
      holder.innerHTML = this._timelineSvg(this._decisions(), this._socHist, this._width || this.offsetWidth || 400);
    }
  }

  _render() {
    if (!this._hass || !this._e) return;
    this._width = this.offsetWidth || this._width || 400;
    const decisions = this._decisions();
    const missing = lsMissingEntities(this._hass, [this._e.plan, this._e.soc]);

    this.shadowRoot.innerHTML = `
      <style>
        ${LS_BASE_CSS}
        h2 { font-size: 14px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
             color: var(--secondary-text-color); margin: 0 0 8px; }
        .ls-timeline { margin-top: 4px; }
        .ls-dw-label { font-size: 10px; font-weight: 700; letter-spacing: 0.12em; fill: rgba(251,146,60,0.9); }
        .ls-ref-label, .ls-axis-label, .ls-now-label { font-size: 10px; fill: var(--secondary-text-color); }
        .ls-now-label { fill: #ef4444; font-weight: 700; }
        .ls-legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
        .ls-legend span { display: inline-flex; align-items: center; gap: 5px; }
        .ls-sw { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
        .ls-box { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
      </style>
      <ha-card>
        <h2>${lsEsc(this._cfg.title)}</h2>
        <div class="ls-timeline">${this._timelineSvg(decisions, this._socHist, this._width)}</div>
        <div class="ls-legend">
          <span><span class="ls-sw" style="background:var(--primary-text-color)"></span>actual</span>
          <span><span class="ls-sw" style="background:#f59e0b"></span>plan</span>
          <span><span class="ls-box" style="background:${LS_ACTION_META.charge_grid_normal.color}"></span>charge</span>
          <span><span class="ls-box" style="background:${LS_ACTION_META.charge_grid_boost.color}"></span>boost</span>
          <span><span class="ls-box" style="background:${LS_ACTION_META.proactive_export.color}"></span>export</span>
          <span><span class="ls-box" style="background:repeating-linear-gradient(45deg, rgba(251,146,60,0.5) 0 2px, transparent 2px 5px)"></span>peak window</span>
          <span><span class="ls-box" style="background:rgba(52,211,153,0.25)"></span>cheap power</span>
        </div>
        ${lsSetupHint(missing)}
      </ha-card>`;

    this._ensureHistory();
  }
}

/* ============================================================ register === */

Object.assign(LocalShiftPlanTimelineCard.prototype, LS_FULL_WIDTH);

if (!customElements.get("localshift-plan-timeline"))
  customElements.define("localshift-plan-timeline", LocalShiftPlanTimelineCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "localshift-plan-timeline")) {
  window.customCards.push({
    type: "localshift-plan-timeline",
    name: "LocalShift Plan Timeline",
    description:
      "Forward optimizer plan: price bands, peak window, planned actions and SOC (actual + planned).",
  });
}

console.info(`%c LOCALSHIFT-CARDS %c ${LS_VERSION} `,
  "color:#0b1220;background:#34d399;font-weight:700",
  "color:#34d399;background:#0b1220");
