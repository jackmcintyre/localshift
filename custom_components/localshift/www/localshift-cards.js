/**
 * LocalShift Cards — bespoke Lovelace cards for the LocalShift battery optimizer.
 *
 * Cards:
 *   custom:localshift-command-card   — verdict headline, live power flows, and the
 *                                      fused plan timeline (prices + actions + SOC + peak window)
 *   custom:localshift-decisions-card — "why did it do that" feed from the decision log
 *   custom:localshift-money-card     — today's net cost + 7-day history bars
 *
 * Install (no HACS needed):
 *   1. Copy this file to <ha-config>/www/localshift/localshift-cards.js
 *   2. Settings → Dashboards → Resources → Add:
 *        URL:  /local/localshift/localshift-cards.js
 *        Type: JavaScript module
 *      (or add it via the lovelace resources API)
 *
 * All entity ids are configurable per card:
 *   type: custom:localshift-command-card
 *   entities:
 *     soc: sensor.my_home_percentage_charged   # Tesla/Teslemetry
 *     buy_price: sensor.100h_general_price     # Amber Electric
 *     ...
 *   dw_start: "15:00"   # demand window (peak) start, local time
 *   dw_end: "21:00"
 *
 * Note: deploy.sh copies this file with the integration, but HA only serves
 * /local/ from <ha-config>/www — copy it there (step 1) when updating.
 */

const LS_VERSION = "1.0.0";

const LS_ENTITIES = {
  soc: "sensor.my_home_percentage_charged",
  battery_power: "sensor.my_home_battery_power",
  grid_power: "sensor.my_home_grid_power",
  solar_power: "sensor.my_home_solar_power",
  load_power: "sensor.my_home_load_power",
  buy_price: "sensor.100h_general_price",
  sell_price: "sensor.100h_feed_in_price",
  cheap_price: "sensor.localshift_price_cheap_effective",
  plan: "sensor.localshift_optimizer_plan_detailed",
  summary: "sensor.localshift_optimizer_summary",
  plan_grid: "sensor.localshift_optimizer_plan_grid",
  mode: "select.localshift_battery_mode",
  automation: "switch.localshift_automation_enabled",
  dry_run: "switch.localshift_dry_run",
  override: "binary_sensor.localshift_tesla_override_active",
  integration: "sensor.localshift_integration_status",
  target: "number.localshift_battery_target",
  dw_active: "binary_sensor.localshift_demand_window",
  decision_log: "sensor.localshift_decision_log",
  cost: "sensor.localshift_cost_electricity_net",
};

const LS_ACTION_META = {
  charge_grid_normal: { color: "#34d399", label: "Charge" },
  charge_grid_boost: { color: "#fbbf24", label: "Boost" },
  proactive_export: { color: "#a78bfa", label: "Export" },
  spike_discharge: { color: "#f87171", label: "Sell" },
  hold: { color: "rgba(148,163,184,0.22)", label: "Hold" },
};

const LS_MODE_HEADLINE = {
  self_consumption: "Running on sun & stored energy",
  grid_charging: "Charging on cheap power",
  boost_charging: "Boost charging before the peak",
  spike_discharge: "Selling into the spike",
  proactive_export: "Exporting ahead of negative prices",
  demand_block: "Defending the peak window",
  hold: "Holding steady",
  automatic: "Optimizer in control",
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
function lsOn(hass, id) {
  return lsState(hass, id) === "on";
}
function lsEsc(str) {
  return String(str ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}
function lsCents(v) {
  return Number.isFinite(v) ? `${(v * 100).toFixed(1)}¢` : "—";
}
function lsMoney(v) {
  if (!Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}
function lsKw(v) {
  return Number.isFinite(v) ? `${Math.abs(v).toFixed(1)} kW` : "—";
}
function lsHHMM(d) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
function lsRelTime(iso) {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return lsEsc(String(iso).slice(11, 16));
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 24 * 60) return lsHHMM(new Date(t));
  return new Date(t).toLocaleDateString([], { weekday: "short" }) + " " + lsHHMM(new Date(t));
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
  if (hit && hit.pending) return hit.data || [];
  cache[key] = { at: hit ? hit.at : 0, data: hit ? hit.data : [], pending: true };
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
    cache[key] = { at: Date.now(), data, pending: false };
    return data;
  } catch (e) {
    cache[key] = { at: Date.now(), data: (hit && hit.data) || [], pending: false };
    return cache[key].data;
  }
}

const LS_BASE_CSS = `
  :host { display: block; }
  ha-card { padding: 16px; overflow: hidden; }
  .ls-muted { color: var(--secondary-text-color); }
  .ls-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .ls-pill {
    font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
    padding: 3px 9px; border-radius: 999px; line-height: 1.4;
  }
  .ls-pill.red { background: rgba(248,113,113,0.18); color: #f87171; }
  .ls-pill.amber { background: rgba(251,191,36,0.16); color: #d99e16; }
  .ls-pill.violet { background: rgba(167,139,250,0.16); color: #a78bfa; }
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

/* ======================================================== command card === */

class LocalShiftCommandCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._histCache = {};
    this._width = 0;
  }

  setConfig(config) {
    this._e = { ...LS_ENTITIES, ...(config.entities || {}) };
    this._cfg = {
      dw_start: "15:00",
      dw_end: "21:00",
      price_high: 0.3, // $/kWh — amber tint above this
      price_spike: 0.6, // $/kWh — red tint above this
      invert_battery: false, // set true if your battery sensor is positive-when-charging
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
    const watch = [
      e.plan, e.soc, e.mode, e.automation, e.dry_run, e.override, e.integration,
      e.buy_price, e.sell_price, e.cheap_price, e.battery_power, e.grid_power,
      e.solar_power, e.load_power, e.cost, e.summary, e.decision_log,
    ];
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
    return 8;
  }

  /* ---- data assembly ---- */

  _decisions() {
    const raw = lsAttr(this._hass, this._e.plan, "decisions", []) || [];
    return raw
      .map((d) => ({ ...d, t: new Date(d.timestamp_iso).getTime() }))
      .filter((d) => Number.isFinite(d.t));
  }

  _narrative(decisions) {
    const hass = this._hass;
    const e = this._e;
    const mode = lsState(hass, e.mode) || "unknown";
    const automationOn = lsOn(hass, e.automation);
    if (!automationOn) {
      return {
        headline: "Manual control",
        sub: "Automation is off — LocalShift isn't steering the battery.",
        dotColor: "#f87171",
      };
    }
    let headline = LS_MODE_HEADLINE[mode] || "Optimizer in control";
    if (mode === "automatic" && decisions.length) {
      const a = decisions[0].action;
      if (a && a !== "hold") headline = `${LS_ACTION_META[a]?.label || a} — optimizer in control`;
    }
    const reason = lsAttr(hass, e.decision_log, "reason", "");
    const colorByMode = {
      grid_charging: "#34d399",
      boost_charging: "#fbbf24",
      spike_discharge: "#f87171",
      proactive_export: "#a78bfa",
      demand_block: "#fb923c",
    };
    return {
      headline,
      sub: reason ? String(reason) : "",
      dotColor: colorByMode[mode] || "#34d399",
    };
  }

  _nextChange(decisions) {
    if (!decisions.length) return null;
    const cur = decisions[0].action;
    for (const d of decisions) {
      if (d.action !== cur) {
        return { action: d.action, at: new Date(d.t) };
      }
    }
    return null;
  }

  _pills() {
    const hass = this._hass;
    const e = this._e;
    const pills = [];
    if (!lsOn(hass, e.automation)) pills.push(`<span class="ls-pill red">automation off</span>`);
    if (lsOn(hass, e.dry_run)) pills.push(`<span class="ls-pill amber">dry run</span>`);
    if (lsOn(hass, e.override)) pills.push(`<span class="ls-pill red">tesla override</span>`);
    const integ = lsState(hass, e.integration);
    if (integ && integ !== "ok") pills.push(`<span class="ls-pill amber">health: ${lsEsc(integ)}</span>`);
    const success = lsAttr(hass, e.summary, "success", true);
    if (success === false) pills.push(`<span class="ls-pill red">optimizer error</span>`);
    const computed = lsAttr(hass, e.plan, "computed_at");
    if (computed) {
      const age = (Date.now() - new Date(computed).getTime()) / 60000;
      if (Number.isFinite(age) && age > 30)
        pills.push(`<span class="ls-pill amber">plan ${Math.round(age)}m old</span>`);
    }
    return pills.join("");
  }

  /* ---- timeline SVG ---- */

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
      1: "rgba(52,211,153,0.12)",
      2: "rgba(251,146,60,0.10)",
      3: "rgba(248,113,113,0.14)",
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

    // actual SOC (history)
    if (socHist && socHist.length > 1) {
      const pts = socHist
        .filter((p) => p.t >= t0 && p.t <= now)
        .map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`)
        .join(" ");
      if (pts) svg += `<polyline points="${pts}" fill="none" stroke="var(--primary-text-color)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    }

    // planned SOC (dashed, future)
    if (inWin.length > 1) {
      const pts = inWin.map((d) => `${x(d.t).toFixed(1)},${y(d.predicted_soc_pct).toFixed(1)}`).join(" ");
      svg += `<polyline points="${pts}" fill="none" stroke="#f59e0b" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round"/>`;
    }

    // now marker
    svg += `<line x1="${x(now)}" y1="${plotTop - 4}" x2="${x(now)}" y2="${laneBot}" stroke="#ef4444" stroke-width="1.5"/>`;
    svg += `<text x="${x(now)}" y="${plotTop - 6}" text-anchor="middle" class="ls-now-label">now</text>`;

    // time axis labels (every 6 h, snapped)
    const firstTick = Math.ceil(t0 / (6 * 3600e3)) * 6 * 3600e3;
    for (let t = firstTick; t <= t1; t += 6 * 3600e3) {
      const d = new Date(t);
      const lab = d.getHours() === 0
        ? d.toLocaleDateString([], { weekday: "short" })
        : lsHHMM(d);
      svg += `<text x="${x(t)}" y="${labelY}" text-anchor="middle" class="ls-axis-label">${lab}</text>`;
      svg += `<line x1="${x(t)}" y1="${laneBot + 2}" x2="${x(t)}" y2="${laneBot + 7}" stroke="rgba(148,163,184,0.4)"/>`;
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

  /* ---- flows + battery glyph ---- */

  _batteryGlyph(soc, target) {
    const w = 46, h = 20;
    const fillW = Math.max(1, (Math.min(100, soc) / 100) * (w - 6));
    const color = soc < 20 ? "#f87171" : soc < 50 ? "#fbbf24" : "#34d399";
    const tickX = Number.isFinite(target) ? 2 + (target / 100) * (w - 6) : null;
    return `
      <svg width="${w + 4}" height="${h}" style="vertical-align:-4px">
        <rect x="0.5" y="0.5" width="${w - 1}" height="${h - 1}" rx="4" fill="none" stroke="rgba(148,163,184,0.6)"/>
        <rect x="${w}" y="${h / 2 - 4}" width="3" height="8" rx="1" fill="rgba(148,163,184,0.6)"/>
        <rect x="2.5" y="2.5" width="${fillW}" height="${h - 5}" rx="2.5" fill="${color}"/>
        ${tickX ? `<line x1="${tickX}" y1="1" x2="${tickX}" y2="${h - 1}" stroke="var(--primary-text-color)" stroke-width="1.5" opacity="0.8"/>` : ""}
      </svg>`;
  }

  _flows() {
    const hass = this._hass;
    const e = this._e;
    const solar = lsNum(hass, e.solar_power, NaN);
    const load = lsNum(hass, e.load_power, NaN);
    const grid = lsNum(hass, e.grid_power, NaN);
    let batt = lsNum(hass, e.battery_power, NaN);
    if (this._cfg.invert_battery && Number.isFinite(batt)) batt = -batt;
    // convention: battery positive = discharging, grid positive = importing
    const soc = lsNum(hass, e.soc, NaN);
    const target = lsNum(hass, e.target, NaN);

    const cell = (label, value, dir, dirColor) => `
      <div class="ls-flow">
        <div class="ls-flow-label">${label}</div>
        <div class="ls-flow-value">${value}</div>
        ${dir ? `<div class="ls-flow-dir" style="color:${dirColor}">${dir}</div>` : `<div class="ls-flow-dir">&nbsp;</div>`}
      </div>`;

    const gridDir = !Number.isFinite(grid) || Math.abs(grid) < 0.05 ? "" : grid > 0 ? "importing" : "exporting";
    const battDir = !Number.isFinite(batt) || Math.abs(batt) < 0.05 ? "idle" : batt > 0 ? "discharging" : "charging";
    const battColor = battDir === "charging" ? "#34d399" : battDir === "discharging" ? "#fbbf24" : "var(--secondary-text-color)";

    return `
      <div class="ls-flows">
        ${cell("Solar", lsKw(solar), Number.isFinite(solar) && solar > 0.05 ? "generating" : "", "#fbbf24")}
        ${cell("Home", lsKw(load), "", "")}
        ${cell("Grid", lsKw(grid), gridDir, gridDir === "importing" ? "#f87171" : "#a78bfa")}
        <div class="ls-flow">
          <div class="ls-flow-label">Battery</div>
          <div class="ls-flow-value">${Number.isFinite(soc) ? Math.round(soc) + "%" : "—"} ${this._batteryGlyph(soc, target)}</div>
          <div class="ls-flow-dir" style="color:${battColor}">${battDir}${Number.isFinite(batt) && Math.abs(batt) >= 0.05 ? " · " + lsKw(batt) : ""}</div>
        </div>
      </div>`;
  }

  _chips(decisions) {
    const hass = this._hass;
    const e = this._e;
    const buy = lsNum(hass, e.buy_price, NaN);
    const sell = lsNum(hass, e.sell_price, NaN);
    const cheap = lsNum(hass, e.cheap_price, NaN);
    const target = lsNum(hass, e.target, NaN);
    const dwEntry = parseFloat(lsAttr(hass, e.summary, "dw_entry_soc_pct"));
    const shortfall = parseFloat(lsAttr(hass, e.summary, "terminal_shortfall_pct", 0));
    const net = lsNum(hass, e.cost, NaN);
    const next = this._nextChange(decisions);
    const dwActive = lsOn(hass, e.dw_active);

    const chip = (label, value, cls = "") => `
      <div class="ls-chip ${cls}">
        <div class="ls-chip-label">${label}</div>
        <div class="ls-chip-value">${value}</div>
      </div>`;

    const buyCls = Number.isFinite(buy) && Number.isFinite(cheap) && buy <= cheap ? "good" : Number.isFinite(buy) && buy >= this._cfg.price_high ? "bad" : "";
    let readyVal, readyCls;
    if (dwActive) {
      readyVal = "in peak now";
      readyCls = "warn";
    } else if (Number.isFinite(dwEntry)) {
      const ok = !Number.isFinite(shortfall) || shortfall <= 5.0;
      readyVal = `${dwEntry.toFixed(0)}%${Number.isFinite(target) ? " / " + Math.round(target) + "%" : ""} ${ok ? "✓" : "▲ " + shortfall.toFixed(0) + "% short"}`;
      readyCls = ok ? "good" : "bad";
    } else {
      readyVal = "—";
      readyCls = "";
    }

    return `
      <div class="ls-chips">
        ${chip("Buy now", lsCents(buy), buyCls)}
        ${chip("Sell now", lsCents(sell))}
        ${chip("Peak entry", readyVal, readyCls)}
        ${chip("Next move", next ? `${LS_ACTION_META[next.action]?.label || next.action} ${lsHHMM(next.at)}` : "none planned")}
        ${chip("Today", Number.isFinite(net) ? (net <= 0 ? lsMoney(-net) + " earned" : lsMoney(net) + " spent") : "—", Number.isFinite(net) && net <= 0 ? "good" : "")}
      </div>`;
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
    const n = this._narrative(decisions);
    const missing = lsMissingEntities(this._hass, [
      this._e.plan, this._e.soc, this._e.mode, this._e.buy_price,
    ]);

    this.shadowRoot.innerHTML = `
      <style>
        ${LS_BASE_CSS}
        .ls-head { display: flex; align-items: flex-start; gap: 10px; }
        .ls-dot { width: 12px; height: 12px; border-radius: 50%; margin-top: 9px; flex: none;
                  box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 18%, transparent); }
        .ls-headline { font-size: 22px; font-weight: 700; line-height: 1.2; letter-spacing: -0.01em; }
        .ls-sub { font-size: 13px; margin-top: 3px; color: var(--secondary-text-color); }
        .ls-flows { display: flex; flex-wrap: wrap; gap: 4px 22px; margin: 14px 0 4px; }
        .ls-flow-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--secondary-text-color); }
        .ls-flow-value { font-size: 17px; font-weight: 600; margin-top: 1px; white-space: nowrap; }
        .ls-flow-dir { font-size: 11px; color: var(--secondary-text-color); }
        .ls-timeline { margin-top: 8px; }
        .ls-dw-label { font-size: 10px; font-weight: 700; letter-spacing: 0.12em; fill: rgba(251,146,60,0.9); }
        .ls-ref-label, .ls-axis-label, .ls-now-label { font-size: 10px; fill: var(--secondary-text-color); }
        .ls-now-label { fill: #ef4444; font-weight: 700; }
        .ls-legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
        .ls-legend span { display: inline-flex; align-items: center; gap: 5px; }
        .ls-sw { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
        .ls-box { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
        .ls-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
        .ls-chip { background: var(--secondary-background-color, rgba(148,163,184,0.08));
                   border-radius: 10px; padding: 7px 12px; min-width: 84px; }
        .ls-chip-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--secondary-text-color); }
        .ls-chip-value { font-size: 15px; font-weight: 650; margin-top: 1px; white-space: nowrap; }
        .ls-chip.good .ls-chip-value { color: #34d399; }
        .ls-chip.bad .ls-chip-value { color: #f87171; }
        .ls-chip.warn .ls-chip-value { color: #fb923c; }
        @media (max-width: 460px) {
          .ls-headline { font-size: 18px; }
          .ls-chip { min-width: 72px; padding: 6px 10px; }
        }
      </style>
      <ha-card>
        <div class="ls-pills">${this._pills()}</div>
        <div class="ls-head">
          <div class="ls-dot" style="background:${n.dotColor}; color:${n.dotColor}"></div>
          <div>
            <div class="ls-headline">${lsEsc(n.headline)}</div>
            ${n.sub ? `<div class="ls-sub">${lsEsc(n.sub)}</div>` : ""}
          </div>
        </div>
        ${this._flows()}
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
        ${this._chips(decisions)}
        ${lsSetupHint(missing)}
      </ha-card>`;

    this._ensureHistory();
  }
}

/* ====================================================== decisions card === */

class LocalShiftDecisionsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }
  setConfig(config) {
    this._cfg = { entity: LS_ENTITIES.decision_log, limit: 8, title: "Why it did that", ...config };
    if (this._hass) this._render();
  }
  set hass(hass) {
    this._hass = hass;
    const s = hass.states[this._cfg?.entity];
    const snap = s ? `${s.state}@${s.last_updated}` : "∅";
    if (snap !== this._snap) {
      this._snap = snap;
      this._render();
    }
  }
  getCardSize() {
    return 4;
  }
  _render() {
    if (!this._hass || !this._cfg) return;
    const hist = (lsAttr(this._hass, this._cfg.entity, "history", []) || []).slice(-this._cfg.limit).reverse();
    const missing = lsMissingEntities(this._hass, [this._cfg.entity]);
    const rows = hist.length
      ? hist
          .map((h, idx) => {
            const ts = h.timestamp || h.time || "";
            return `
            <div class="ls-row ${idx === 0 ? "first" : ""}">
              <div class="ls-rail"><div class="ls-node"></div>${idx < hist.length - 1 ? '<div class="ls-line"></div>' : ""}</div>
              <div class="ls-body">
                <div class="ls-reason">${lsEsc(h.reason || "—")}</div>
                <div class="ls-meta">${lsEsc(lsRelTime(ts))}${h.soc !== undefined ? ` · SOC ${lsEsc(h.soc)}%` : ""}</div>
              </div>
            </div>`;
          })
          .join("")
      : `<div class="ls-muted" style="font-size:13px">No decisions logged yet.</div>`;
    this.shadowRoot.innerHTML = `
      <style>
        ${LS_BASE_CSS}
        h2 { font-size: 14px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
             color: var(--secondary-text-color); margin: 0 0 12px; }
        .ls-row { display: flex; gap: 10px; }
        .ls-rail { display: flex; flex-direction: column; align-items: center; width: 12px; flex: none; }
        .ls-node { width: 8px; height: 8px; border-radius: 50%; background: rgba(148,163,184,0.7); margin-top: 5px; flex: none; }
        .ls-row.first .ls-node { background: #34d399; box-shadow: 0 0 0 3px rgba(52,211,153,0.2); }
        .ls-line { width: 2px; flex: 1; background: rgba(148,163,184,0.2); margin: 3px 0; }
        .ls-body { padding-bottom: 12px; min-width: 0; }
        .ls-reason { font-size: 13.5px; line-height: 1.35; }
        .ls-meta { font-size: 11.5px; color: var(--secondary-text-color); margin-top: 2px; }
      </style>
      <ha-card>
        <h2>${lsEsc(this._cfg.title)}</h2>
        ${rows}
        ${lsSetupHint(missing)}
      </ha-card>`;
  }
}

/* ========================================================== money card === */

class LocalShiftMoneyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._histCache = {};
  }
  setConfig(config) {
    this._cfg = { entity: LS_ENTITIES.cost, plan_grid: LS_ENTITIES.plan_grid, days: 7, title: "Money", ...config };
    if (this._hass) this._render();
  }
  set hass(hass) {
    this._hass = hass;
    const s = hass.states[this._cfg?.entity];
    const snap = s ? `${s.state}@${s.last_updated}` : "∅";
    if (snap !== this._snap) {
      this._snap = snap;
      this._render();
    }
  }
  getCardSize() {
    return 4;
  }

  async _ensureHistory() {
    const now = new Date();
    const start = new Date(now);
    start.setDate(start.getDate() - (this._cfg.days - 1));
    start.setHours(0, 0, 0, 0);
    const data = await lsFetchHistory(
      this._hass, this._histCache, "cost", this._cfg.entity,
      start.getTime(), now.getTime(), 10 * 60e3
    );
    // bucket: last reading per local day
    const byDay = new Map();
    for (const p of data) {
      const d = new Date(p.t);
      byDay.set(d.toDateString(), p.v);
    }
    byDay.set(now.toDateString(), lsNum(this._hass, this._cfg.entity, byDay.get(now.toDateString())));
    const days = [];
    for (let i = this._cfg.days - 1; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      days.push({
        label: d.toLocaleDateString([], { weekday: "narrow" }),
        v: byDay.has(d.toDateString()) ? byDay.get(d.toDateString()) : null,
        today: i === 0,
      });
    }
    const key = JSON.stringify(days);
    if (key !== this._daysKey) {
      this._daysKey = key;
      this._days = days;
      this._renderBars();
    }
  }

  _barsSvg() {
    const days = this._days;
    if (!days || !days.some((d) => d.v !== null)) {
      return `<div class="ls-muted" style="font-size:12px">Collecting history…</div>`;
    }
    const W = 280, H = 96, padB = 16, barW = W / days.length - 8;
    const maxAbs = Math.max(0.5, ...days.map((d) => Math.abs(d.v ?? 0)));
    const zeroY = (H - padB) / 2 + 4;
    const scale = ((H - padB) / 2 - 6) / maxAbs;
    let svg = `<line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="rgba(148,163,184,0.3)"/>`;
    days.forEach((d, idx) => {
      const cx = (idx + 0.5) * (W / days.length);
      if (d.v !== null) {
        const h = Math.max(1.5, Math.abs(d.v) * scale);
        const earned = d.v <= 0;
        const yTop = earned ? zeroY - h : zeroY;
        svg += `<rect x="${cx - barW / 2}" y="${yTop}" width="${barW}" height="${h}" rx="2.5"
                 fill="${earned ? "#34d399" : "#f87171"}" opacity="${d.today ? 1 : 0.65}"/>`;
      }
      svg += `<text x="${cx}" y="${H - 3}" text-anchor="middle" class="ls-bar-label">${lsEsc(d.label)}</text>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="xMidYMid meet">${svg}</svg>`;
  }

  _renderBars() {
    const holder = this.shadowRoot.querySelector(".ls-bars");
    if (holder) holder.innerHTML = this._barsSvg();
  }

  _render() {
    if (!this._hass || !this._cfg) return;
    const net = lsNum(this._hass, this._cfg.entity, NaN);
    const imp = parseFloat(lsAttr(this._hass, this._cfg.entity, "grid_import_cost", NaN));
    const exp = parseFloat(lsAttr(this._hass, this._cfg.entity, "grid_export_revenue", NaN));
    const planNet = lsNum(this._hass, this._cfg.plan_grid, NaN);
    const missing = lsMissingEntities(this._hass, [this._cfg.entity]);
    const earned = Number.isFinite(net) && net <= 0;

    this.shadowRoot.innerHTML = `
      <style>
        ${LS_BASE_CSS}
        h2 { font-size: 14px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
             color: var(--secondary-text-color); margin: 0 0 10px; }
        .ls-big { font-size: 34px; font-weight: 750; letter-spacing: -0.02em; line-height: 1;
                  color: ${earned ? "#34d399" : "var(--primary-text-color)"}; }
        .ls-big small { font-size: 14px; font-weight: 600; color: var(--secondary-text-color); margin-left: 6px; }
        .ls-split { display: flex; gap: 18px; margin: 10px 0 14px; font-size: 12.5px; color: var(--secondary-text-color); }
        .ls-split b { color: var(--primary-text-color); font-weight: 600; }
        .ls-bar-label { font-size: 9px; fill: var(--secondary-text-color); }
      </style>
      <ha-card>
        <h2>${lsEsc(this._cfg.title)}</h2>
        <div class="ls-big">${Number.isFinite(net) ? lsMoney(Math.abs(net)) : "—"}<small>${earned ? "earned today" : "spent today"}</small></div>
        <div class="ls-split">
          <span>imports <b>${Number.isFinite(imp) ? lsMoney(imp) : "—"}</b></span>
          <span>exports <b>${Number.isFinite(exp) ? lsMoney(exp) : "—"}</b></span>
          <span>rest of plan <b>${Number.isFinite(planNet) ? (planNet <= 0 ? lsMoney(-planNet) + " earn" : lsMoney(planNet)) : "—"}</b></span>
        </div>
        <div class="ls-bars">${this._barsSvg()}</div>
        ${lsSetupHint(missing)}
      </ha-card>`;
    this._ensureHistory();
  }
}

/* ============================================================ register === */

if (!customElements.get("localshift-command-card"))
  customElements.define("localshift-command-card", LocalShiftCommandCard);
if (!customElements.get("localshift-decisions-card"))
  customElements.define("localshift-decisions-card", LocalShiftDecisionsCard);
if (!customElements.get("localshift-money-card"))
  customElements.define("localshift-money-card", LocalShiftMoneyCard);

window.customCards = window.customCards || [];
window.customCards.push(
  {
    type: "localshift-command-card",
    name: "LocalShift Command",
    description: "Battery optimizer mission control: status, flows, and the fused plan timeline.",
  },
  {
    type: "localshift-decisions-card",
    name: "LocalShift Decisions",
    description: "Recent optimizer decisions with reasons.",
  },
  {
    type: "localshift-money-card",
    name: "LocalShift Money",
    description: "Today's net electricity cost and 7-day history.",
  }
);

console.info(
  `%c LOCALSHIFT-CARDS %c v${LS_VERSION} `,
  "background:#34d399;color:#000;font-weight:700;border-radius:3px 0 0 3px",
  "background:#1f2937;color:#fff;border-radius:0 3px 3px 0"
);
