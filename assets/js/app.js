/* each map layer is also an audio stem: toggling a layer mutes its voice */
(() => {
"use strict";

const BIRDS = ["Eric", "Nico", "Sanne"];
const COLORS = { Eric: "#a8201a", Nico: "#0a9396", Sanne: "#ec9a29" };
let DURATION = 15; // audio length
let JOURNEY = 15; // the route part of the audio; any time after it is the home ending
const DAY = 86400000;
const STEMS = ["movement", "temperature", "wind"];
const LAYERS = [
  { id: "temperature", name: "NASA temperature image", voice: "Chords", color: "#ff8a3d",
    icon: '<path d="M10 14.5V5a2 2 0 1 1 4 0v9.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>' },
  { id: "wind", name: "Wind near Nico", voice: "30 October 2024 · ERA5", color: "#7cc4ff",
    icon: '<path d="M3 8h11a3 3 0 1 0-3-3"/><path d="M3 12h16a3 3 0 1 1-3 3"/><path d="M3 16h7"/>' },
  { id: "movement", name: "Bird movement", voice: "Melody and beat", color: "#ffffff",
    icon: '<path d="M4 18c3-1 4-5 7-6s5 2 9-6"/><circle cx="20" cy="6" r="1.6"/>' },
];
const BIRD_INFO = {
  Eric: { winter: "Spain", line: "Eric spent the winter inland in Spain" },
  Nico: { winter: "Dakhla", line: "Nico crossed the Bay of Biscay in one day, 793 km, then wintered at Dakhla" },
  Sanne: { winter: "Dakhla", line: "Sanne left last, on 11 November, and also wintered at Dakhla" },
};

const $ = (s, root = document) => root.querySelector(s);
// regenerated audio keeps its file names, so always check the server before using a cached copy
const NO_CACHE = { cache: "no-cache" };
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const parseDay = (s) => Date.parse(`${s.slice(0, 10)}T00:00:00Z`);
const fmtDate = (ms) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(ms);
const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const fmtInt = d3.format(",");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const app = $("#app");
const state = {
  chapter: 0,
  bird: "Eric",
  progress: 0,
  layers: { temperature: true, wind: true, movement: true },
  transform: d3.zoomIdentity,
  frameIndex: -1,
  preview: !reducedMotion,
  rendered: d3.zoomIdentity,
};
const data = {};

async function loadJSON(path, optional = false) {
  try {
    const res = await fetch(path, NO_CACHE);
    if (!res.ok) throw new Error(`${res.status} ${path}`);
    return await res.json();
  } catch (err) {
    if (optional) return null;
    throw err;
  }
}

async function loadData() {
  const [daily, totals, stopovers, stats, world, eic, score] = await Promise.all([
    loadJSON("assets/data/daily.json"),
    loadJSON("assets/data/totals.json"),
    loadJSON("assets/data/stopovers.json"),
    loadJSON("assets/data/stats.json"),
    loadJSON("assets/data/countries-50m.json"),
    loadJSON("assets/data/eic_layer.json"),
    loadJSON("assets/data/journey_score.json"),
  ]);
  let wind = null, gridMeta = null;
  if (Object.values(score.birds).some((bird) => bird.weather_available)) {
    [wind, gridMeta] = await Promise.all([loadJSON("assets/data/era5_wind.json", true), loadJSON("assets/data/era5_wind_grid.json", true)]);
  }
  if (!daily.length || !totals.length || !eic.frames?.length) throw new Error("No journey data available");
  data.score = score;
  DURATION = score.duration;
  JOURNEY = score.journey_seconds ?? DURATION;

  data.stats = stats;
  data.totals = Object.fromEntries(totals.map((r) => [r.bird, r]));
  data.points = {};
  data.routes = {};
  data.stops = {};
  BIRDS.forEach((bird) => {
    const t = data.totals[bird];
    const pts = daily
      .filter((p) => p.bird === bird && p.date >= t.departure_date && p.date <= t.return_date)
      .map((p) => ({ ...p, t: parseDay(p.date) }))
      .sort((a, b) => a.t - b.t);
    data.points[bird] = pts;
    if (pts.length < 2) throw new Error(`Not enough recorded days for ${bird}`);
    // split lines at tracking gaps
    const lines = [];
    let current = [];
    pts.forEach((p) => {
      if (p.gap && current.length) { lines.push(current); current = []; }
      current.push([p.lon, p.lat]);
    });
    if (current.length) lines.push(current);
    data.routes[bird] = { type: "MultiLineString", coordinates: lines.filter((l) => l.length > 1) };
    data.stops[bird] = stopovers.filter((s) => s.bird === bird && s.start.slice(0, 10) >= t.departure_date && s.end.slice(0, 10) <= t.return_date);
  });
  data.maxKm = d3.max(daily, (d) => d.km);

  data.bounds = {
    type: "MultiPoint",
    coordinates: [[stats.lon_min - 1.5, stats.lat_min - 1.5], [stats.lon_max + 1.5, stats.lat_max + 1.5]],
  };
  data.land = topojson.feature(world, world.objects.land);
  data.borders = topojson.mesh(world, world.objects.countries, (a, b) => a !== b);
  data.graticule = d3.geoGraticule10();

  data.eic = eic;
  data.eicMercN = mercY(eic.bounds.north);
  data.eicMercS = mercY(eic.bounds.south);
  data.frames = new Array(eic.frames.length);
  data.frameImages = new Map();
  data.framePixels = new Map();

  data.wind = null;
  if (wind?.rows?.length) {
    data.wind = {};
    wind.rows.forEach((r) => { (data.wind[r.bird] ||= {})[r.date] = r; });
    data.windMax = wind.speed_max || 20;
    data.tailMax = wind.tail_abs_max || 15;
  }
  data.grid = null;
  if (gridMeta) {
    try {
      const buf = await fetch("assets/data/era5_wind_grid.bin", NO_CACHE).then((r) => { if (!r.ok) throw new Error(); return r.arrayBuffer(); });
      data.grid = { ...gridMeta, values: new Int8Array(buf), dayIndex: new Map(gridMeta.dates.map((d, i) => [d, i])) };
    } catch { data.grid = null; }
  }
  data.hasWind = Boolean(data.wind || data.grid);
  if (!data.hasWind) state.layers.wind = false;
}

function birdSpan(bird = state.bird) {
  const t = data.totals[bird];
  return [parseDay(t.departure_date), parseDay(t.return_date)];
}
function progressToTime(p, bird = state.bird) { const [a, b] = birdSpan(bird); return lerp(a, b, p); }
function timeToProgress(ms, bird = state.bird) { const [a, b] = birdSpan(bird); return clamp((ms - a) / (b - a), 0, 1); }

const bisectT = d3.bisector((d) => d.t).right;
function sampleRoute(bird, ms) {
  const pts = data.points[bird];
  const i = clamp(bisectT(pts, ms), 1, pts.length - 1);
  const a = pts[i - 1], b = pts[i];
  const f = clamp((ms - a.t) / (b.t - a.t || 1), 0, 1);
  const near = f < 0.5 ? a : b;
  if (b.gap) return { ...a, gap: true, near };
  return { ...a, lat: lerp(a.lat, b.lat, f), lon: lerp(a.lon, b.lon, f), km: lerp(a.km, b.km, f), near };
}

const svg = d3.select("#layer-vector");
const viewport = svg.select(".g-viewport");
const eicCanvas = $("#layer-eic");
const windCanvas = $("#layer-wind");
let W = 0, H = 0, DPR = 1, base;

function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)); }

let projectionCache;
function projection() {
  const t = state.transform;
  if (projectionCache?.transform === t && projectionCache.base === base) return projectionCache.value;
  const [tx, ty] = base.translate();
  const value = d3.geoMercator()
    .scale(base.scale() * t.k)
    .translate([t.x + t.k * tx, t.y + t.k * ty])
    .clipExtent([[-40, -40], [W + 40, H + 40]]);
  projectionCache = { transform: t, base, value };
  return value;
}

function resize() {
  W = window.innerWidth; H = window.innerHeight; DPR = Math.min(window.devicePixelRatio || 1, 2);
  [eicCanvas, windCanvas].forEach((c) => { c.width = W * DPR; c.height = H * DPR; });
  svg.attr("viewBox", `0 0 ${W} ${H}`);
  base = d3.geoMercator().fitExtent([[0, 0], [W, H]], data.bounds);
  renderStatic();
  drawTimeline();
  flyTo(false);
}

let fullRenderTimer;
const zoom = d3.zoom()
  .scaleExtent([0.35, 14])
  .on("zoom", (event) => {
    state.transform = event.transform;
    const previous = state.rendered;
    const ratio = event.transform.k / previous.k;
    viewport.attr("transform", `translate(${event.transform.x - previous.x * ratio},${event.transform.y - previous.y * ratio}) scale(${ratio})`);
    state.frameIndex = -1;
    renderDynamic();
    wind.reset();
    clearTimeout(fullRenderTimer);
    fullRenderTimer = setTimeout(renderStatic, 160);
  });
svg.call(zoom).on("dblclick.zoom", null);

function viewRect() {
  const mobile = W <= 820;
  let x0 = 28, y0 = 76, x1 = W - 28, y1 = H - 28;
  const story = $(".story").getBoundingClientRect();
  const layersOn = !$(".layers").classList.contains("is-hidden");
  const dockOn = !$(".dock").classList.contains("is-hidden");
  if (mobile) {
    y1 = story.top - 12;
    if (layersOn) y0 = $(".layers").getBoundingClientRect().bottom + 12;
  } else {
    x0 = story.right + 36;
    if (layersOn) x1 = W - $(".layers").offsetWidth - 54;
    if (dockOn) y1 = H - $(".dock").offsetHeight - 44;
  }
  if (x1 - x0 < 220) { x0 = 28; x1 = W - 28; }
  if (y1 - y0 < 200) { y0 = 76; y1 = H - 28; }
  return [x0, y0, x1, y1];
}

function flyTo(animate = true) {
  const [x0, y0, x1, y1] = viewRect();
  const [[bx0, by0], [bx1, by1]] = d3.geoPath(base).bounds(data.bounds);
  const k = Math.min((x1 - x0) / (bx1 - bx0), (y1 - y0) / (by1 - by0));
  const tx = x0 + ((x1 - x0) - k * (bx1 - bx0)) / 2 - k * bx0;
  const ty = y0 + ((y1 - y0) - k * (by1 - by0)) / 2 - k * by0;
  const target = d3.zoomIdentity.translate(tx, ty).scale(k);
  (animate && !reducedMotion ? svg.transition().duration(1100).ease(d3.easeCubicInOut) : svg).call(zoom.transform, target);
}

function renderStatic() {
  if (!base) return;
  const proj = projection();
  const path = d3.geoPath(proj);
  clearTimeout(fullRenderTimer);
  state.rendered = state.transform;
  viewport.attr("transform", null);

  svg.select(".g-graticule").selectAll("path").data([data.graticule]).join("path").attr("class", "graticule").attr("d", path);
  const landPath = path(data.land);
  svg.select(".g-land").selectAll("path").data([
    { cls: "land", d: landPath }, { cls: "borders", d: path(data.borders) }, { cls: "coast", d: landPath },
  ]).join("path").attr("class", (d) => d.cls).attr("d", (d) => d.d);

  const routes = svg.select(".g-routes").selectAll("g.route-g").data(BIRDS, (b) => b).join((enter) => {
    const g = enter.append("g").attr("class", "route-g");
    g.append("path").attr("class", "route-casing");
    g.append("path").attr("class", "route");
    return g;
  });
  // keyed parent datum keeps colours on the right bird
  routes.select(".route-casing").attr("d", (b) => path(data.routes[b]));
  routes.select(".route").attr("stroke", (b) => COLORS[b]).attr("d", (b) => path(data.routes[b]));
  // active route last, on top
  routes.sort((a, b) => (a === state.bird) - (b === state.bird));
  updateRouteClasses();

  svg.select(".g-stops").selectAll("circle").data(BIRDS.flatMap((b) => data.stops[b].map((s) => ({ ...s, b }))))
    .join("circle").attr("class", "stop")
    .attr("stroke", (d) => COLORS[d.b])
    .attr("r", (d) => clamp(Math.sqrt(d.days) * 1.5 * Math.sqrt(state.transform.k), 3, 22))
    .attr("cx", (d) => proj([d.lon, d.lat])[0]).attr("cy", (d) => proj([d.lon, d.lat])[1]);

  const places = [
    { name: "Zeebrugge, Belgium", lon: d3.median(BIRDS, (b) => data.totals[b].start_lon), lat: d3.median(BIRDS, (b) => data.totals[b].start_lat), dx: 10, anchor: "start" },
    { name: "Spain", lon: data.totals.Eric.southmost_lon, lat: data.totals.Eric.southmost_lat, dx: -12, anchor: "end" },
    { name: "Dakhla", lon: data.totals.Nico.southmost_lon, lat: data.totals.Nico.southmost_lat, dx: -12, anchor: "end" },
  ];
  const pg = svg.select(".g-places").selectAll("g").data(places).join((e) => {
    const g = e.append("g");
    g.append("circle").attr("class", "place-dot").attr("r", 2.2);
    g.append("text").attr("class", "place").attr("dy", "0.35em");
    return g;
  }).attr("transform", (d) => `translate(${proj([d.lon, d.lat])})`);
  pg.select("text").attr("x", (d) => d.dx).attr("text-anchor", (d) => d.anchor).text((d) => d.name);

  state.frameIndex = -1; // force raster redraw
  renderDynamic();
}

function updateRouteClasses() {
  svg.selectAll(".route, .route-casing").classed("is-active", function () { return d3.select(this.parentNode).datum() === state.bird || app.dataset.all === "on"; });
  svg.selectAll(".stop").classed("is-active", (d) => d.b === state.bird && app.dataset.all !== "on");
}

function imageFrame(index) {
  let img = data.frameImages.get(index);
  if (!img) {
    img = new Image(); img.decoding = "async"; img.src = data.eic.frames[index];
    data.frames[index] = img;
  }
  data.frameImages.delete(index);
  data.frameImages.set(index, img);
  while (data.frameImages.size > 8) {
    const oldest = data.frameImages.keys().next().value;
    data.frameImages.delete(oldest); data.framePixels.delete(oldest); data.frames[oldest] = null;
  }
  return img;
}

function drawRaster(position) {
  const ctx = eicCanvas.getContext("2d");
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  if (!state.layers.temperature) { ctx.clearRect(0, 0, W, H); return; }
  const index = Math.floor(position);
  const amount = reducedMotion ? 0 : position - index;
  const img = imageFrame(index);
  if (!img.complete || !img.naturalWidth) {
    if (!img.renderPending) {
      img.renderPending = true;
      img.addEventListener("load", () => { img.renderPending = false; state.frameIndex = -1; renderDynamic(); }, { once: true });
    }
    return;
  }
  ctx.clearRect(0, 0, W, H);
  const proj = projection();
  const b = data.eic.bounds;
  const [x0, y0] = proj([b.west, b.north]);
  const [x1, y1] = proj([b.east, b.south]);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.globalAlpha = 1;
  ctx.drawImage(img, x0, y0, x1 - x0, y1 - y0);
  const next = imageFrame(Math.min(index + 1, data.frames.length - 1));
  if (amount > 0 && next?.complete && next.naturalWidth) {
    ctx.globalAlpha = amount;
    ctx.drawImage(next, x0, y0, x1 - x0, y1 - y0);
  }
  ctx.globalAlpha = 1;
}

// EIC colour at lon/lat, read from the displayed crop
function eicColorAt(index, lon, lat) {
  const img = imageFrame(index);
  if (!img?.complete || !img.naturalWidth) return null;
  let px = data.framePixels.get(index);
  if (!px) {
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const cx = c.getContext("2d", { willReadFrequently: true });
    cx.drawImage(img, 0, 0);
    px = { w: c.width, h: c.height, d: cx.getImageData(0, 0, c.width, c.height).data };
    data.framePixels.set(index, px);
  }
  const b = data.eic.bounds;
  const x = Math.round(((lon - b.west) / (b.east - b.west)) * px.w);
  const y = Math.round(((data.eicMercN - mercY(lat)) / (data.eicMercN - data.eicMercS)) * px.h);
  let r = 0, g = 0, bl = 0, n = 0;
  for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
    const xx = clamp(x + dx, 0, px.w - 1), yy = clamp(y + dy, 0, px.h - 1);
    const o = (yy * px.w + xx) * 4;
    r += px.d[o]; g += px.d[o + 1]; bl += px.d[o + 2]; n++;
  }
  const rgb = [r / n, g / n, bl / n];
  const values = data.score.birds[state.bird].columns.warmth;
  const exact = state.progress * (values.length - 1);
  const lo = Math.floor(exact), hi = Math.min(lo + 1, values.length - 1);
  const warmth = lerp(values[lo], values[hi], exact - lo);
  return { rgb, warmth };
}
function warmthLabel(w) { return w < .62 ? "Cooler" : w > .72 ? "Warmer" : "Mid-range"; } // map turns orange at 2/3

function windAt(bird, ms, lon, lat) {
  const day = isoDay(ms);
  const row = data.wind?.[bird]?.[day];
  if (!data.grid) return row && (!row.valid_until || ms <= Date.parse(row.valid_until)) ? row : null;
  const uv = gridSample(day, lon, lat, ms);
  if (!uv) return null;
  const [u, v] = uv;
  const heading = row?.heading_deg === undefined ? null : row.heading_deg * Math.PI / 180;
  return { u, v, speed: Math.hypot(u, v), dir_from: (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360, tail: heading === null ? null : u * Math.sin(heading) + v * Math.cos(heading) };
}

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

const GULL_SVG = `
  <g class="wing wing-l"><path class="wing-fill" d="M-2.2,-1.8 C-8,-6.4 -16.5,-6.6 -24,-1.2 C-18.5,-2.2 -13,-0.4 -8.4,2.6 C-6,4.1 -3.4,4.2 -2.2,3.2 Z"/><path class="wing-tip" d="M-24,-1.2 C-21.6,-3 -19,-4 -16.8,-4.4 C-17.6,-2.6 -18.4,-1.8 -19.4,-1.4 C-21,-1.7 -22.6,-1.6 -24,-1.2 Z"/></g>
  <g class="wing wing-r"><path class="wing-fill" d="M2.2,-1.8 C8,-6.4 16.5,-6.6 24,-1.2 C18.5,-2.2 13,-0.4 8.4,2.6 C6,4.1 3.4,4.2 2.2,3.2 Z"/><path class="wing-tip" d="M24,-1.2 C21.6,-3 19,-4 16.8,-4.4 C17.6,-2.6 18.4,-1.8 19.4,-1.4 C21,-1.7 22.6,-1.6 24,-1.2 Z"/></g>
  <path class="gull-tail" d="M-2.6,6.4 L0,10.2 L2.6,6.4 Z"/>
  <ellipse class="gull-body" cx="0" cy="0.8" rx="3.1" ry="7.6"/>
  <circle class="gull-head" cx="0" cy="-6.9" r="2.5"/>
  <path class="gull-beak" d="M-0.75,-9.1 L0,-11.6 L0.75,-9.1 Z"/>`;

function createBird(parent, bird, ghost = false) {
  const g = parent.append("g").attr("class", `bird${ghost ? " ghost-bird" : ""}`).style("--c", COLORS[bird]);
  g.append("circle").attr("class", "bird-halo").attr("r", ghost ? 15 : 20);
  const rot = g.append("g");
  rot.append("g").attr("transform", `scale(${ghost ? 1 : 1.35})`).append("g").attr("class", "gull").html(GULL_SVG);
  const tag = g.append("g").attr("class", "bird-tag");
  const rect = tag.append("rect").attr("rx", 7).attr("height", 18).attr("y", ghost ? 17 : 23);
  const text = tag.append("text").attr("y", ghost ? 29.5 : 35.5).attr("text-anchor", "middle");
  text.append("tspan").attr("class", "tag-name").text(bird);
  const info = text.append("tspan").attr("dx", 5);
  const wings = g.selectAll(".wing").nodes();
  return { bird, g, rot, rect, text, info, wings, heading: 180, label: "", movement: "", phase: Math.random(), rate: 1.2, fold: 0, targetRate: 1.2, targetFold: 0 };
}

const activeGull = createBird(svg.select(".g-dot"), "Eric");
const otherGulls = {};
function placeBird(gull, project, ms) {
  const point = sampleRoute(gull.bird, ms);
  const [x, y] = project([point.lon, point.lat]);
  const ahead = sampleRoute(gull.bird, ms + DAY * .8);
  const [ax, ay] = project([ahead.lon, ahead.lat]);
  if (Math.hypot(ax - x, ay - y) > .8) {
    const target = Math.atan2(ax - x, -(ay - y)) * 180 / Math.PI;
    const turn = ((target - gull.heading) % 360 + 540) % 360 - 180;
    gull.heading += turn * (reducedMotion ? 1 : .2);
  }
  gull.g.attr("transform", `translate(${x.toFixed(1)},${y.toFixed(1)})`);
  gull.rot.attr("transform", `rotate(${gull.heading.toFixed(1)})`);
  const movement = point.gap ? "gap" : point.km < 30 ? "low" : "moving";
  if (movement !== gull.movement) {
    gull.movement = movement;
    gull.g.classed("is-gap", point.gap).classed("is-resting", point.km < 30);
  }
  gull.targetRate = 1 / (2 * clamp(.6 - point.km / 1100 * .45, .15, .6));
  gull.targetFold = point.gap || point.km < 30 ? 1 : 0;
  const label = point.gap ? "unknown position" : `${fmtInt(Math.round(point.km))} km/day`;
  if (label !== gull.label) {
    gull.label = label; gull.info.text(label);
    const width = (gull.text.node().getComputedTextLength?.() || 120) + 16;
    gull.rect.attr("x", -width / 2).attr("width", width);
  }
}

function renderDynamic() {
  if (!base) return;
  const proj = projection();
  const bird = state.bird;
  const ms = progressToTime(state.progress);
  const p = sampleRoute(bird, ms);
  const [x, y] = proj([p.lon, p.lat]);

  const framePosition = clamp(state.progress, 0, 1) * (data.frames.length - 1);
  const frame = Math.floor(framePosition);
  if (framePosition !== state.frameIndex) { state.frameIndex = framePosition; drawRaster(framePosition); }

  if (activeGull.bird !== bird) {
    activeGull.bird = bird; activeGull.label = ""; activeGull.g.style("--c", COLORS[bird]);
    activeGull.text.select(".tag-name").text(bird);
  }
  placeBird(activeGull, proj, ms);
  const showAll = state.preview || app.dataset.all === "on" || state.chapter === 0;
  BIRDS.forEach((name) => {
    if (showAll && name !== bird) {
      otherGulls[name] ||= createBird(svg.select(".g-preview"), name, true);
      otherGulls[name].g.attr("display", null);
      placeBird(otherGulls[name], proj, progressToTime(state.progress, name));
    } else otherGulls[name]?.g.attr("display", "none");
  });

  // arrow points where the air goes
  const w = state.layers.wind ? windAt(bird, ms, p.lon, p.lat) : null;
  const arrow = svg.select(".g-arrow");
  if (w) {
    const len = 18 + clamp(w.speed / (data.windMax || 20), 0, 1) * 52;
    const a = Math.atan2(-w.v, w.u);
    const tx = x + Math.cos(a) * len, ty = y + Math.sin(a) * len;
    const head = w.tail !== null && w.tail < -1;
    arrow.selectAll("line").data([0]).join("line").attr("class", `arrow-line${head ? " is-head" : ""}`)
      .attr("x1", x).attr("y1", y).attr("x2", tx).attr("y2", ty);
    const s = 0.5, bk = 8;
    arrow.selectAll("path").data([0]).join("path").attr("class", "arrow-head")
      .attr("d", `M${tx},${ty}L${tx - Math.cos(a - s) * bk},${ty - Math.sin(a - s) * bk}L${tx - Math.cos(a + s) * bk},${ty - Math.sin(a + s) * bk}Z`);
  } else {
    arrow.selectAll("*").remove();
  }

  $("#ro-date").textContent = windDemo.active ? `${fmtDate(ms)} · ${String(new Date(ms).getUTCHours()).padStart(2, "0")}:00` : fmtDate(ms);
  $("#ro-km").textContent = p.gap ? "Tracking gap" : `${fmtInt(Math.round(p.km))} km/day`;
  const c = eicColorAt(frame, p.lon, p.lat);
  const sw = $("#ro-temp .swatch");
  if (c) {
    sw.style.background = `rgb(${c.rgb.map(Math.round).join(",")})`;
    $("#ro-temp span").textContent = warmthLabel(c.warmth);
  }
  if (w) {
    const from = COMPASS[Math.round(w.dir_from / 45) % 8];
    $("#ro-wind").textContent = `${Math.round(w.speed * 3.6)} km/h from ${from}`;
  } else {
    $("#ro-wind").textContent = !state.layers.wind ? "Off" : "No data here";
  }
  updatePlayhead();
}

function gridSample(day, lon, lat, sampleTime) {
  const g = data.grid;
  if (!g) return null;
  let d = g.dayIndex.get(day), next = d, blend = 0;
  if (d === undefined) return null;
  if (g.times?.length) {
    const ms = sampleTime ?? (windDemo.active ? windDemo.observedTime() : progressToTime(state.progress));
    const start = Date.parse(g.times[0]), end = Date.parse(g.times.at(-1));
    if (ms < start || ms > end) return null;
    const exact = (ms - start) / (end - start || 1) * (g.times.length - 1);
    d = Math.floor(exact); next = Math.min(d + 1, g.times.length - 1); blend = exact - d;
  }
  const nLat = g.lats.length, nLon = g.lons.length;
  const fi = (lat - g.lats[0]) / (g.lats[1] - g.lats[0]);
  const fj = (lon - g.lons[0]) / (g.lons[1] - g.lons[0]);
  if (fi < 0 || fj < 0 || fi > nLat - 1 || fj > nLon - 1) return null;
  const i = Math.min(Math.floor(fi), nLat - 2), j = Math.min(Math.floor(fj), nLon - 2);
  const y = fi - i, x = fj - j;
  const at = (comp, ii, jj) => lerp(g.values[((d * 2 + comp) * nLat + ii) * nLon + jj], g.values[((next * 2 + comp) * nLat + ii) * nLon + jj], blend) / g.scale;
  const bil = (comp) => (at(comp, i, j) * (1 - x) + at(comp, i, j + 1) * x) * (1 - y) + (at(comp, i + 1, j) * (1 - x) + at(comp, i + 1, j + 1) * x) * y;
  return [bil(0), bil(1)];
}

const wind = (() => {
  let particles = [];
  const ctx = windCanvas.getContext("2d");
  function spawn(proj) {
    for (let tries = 0; tries < 8; tries++) {
      const x = Math.random() * W, y = Math.random() * H;
      const ll = proj.invert([x, y]);
      if (ll && gridSample(isoDay(progressToTime(state.progress)), ll[0], ll[1])) return { lon: ll[0], lat: ll[1], age: Math.random() * 80 };
    }
    return { lon: 0, lat: 0, age: 999 };
  }
  function reset() {
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    particles = [];
  }
  function step() {
    if (!data.grid || !state.layers.wind || app.dataset.wind !== "on" || reducedMotion) { if (particles.length) reset(); return; }
    if (!audio.playing && !state.preview && !pitch.running && !windDemo.playing) return;
    if (!data.grid.dayIndex.has(isoDay(progressToTime(state.progress)))) { if (particles.length) reset(); return; }
    const proj = projection();
    const target = Math.round(clamp((W * H) / 900, 400, 2200));
    while (particles.length < target) particles.push(spawn(proj));
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = "rgba(0,0,0,0.9)";
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "source-over";
    ctx.lineWidth = 1.1;
    ctx.strokeStyle = "rgba(225,240,255,0.55)";
    ctx.beginPath();
    const day = isoDay(progressToTime(state.progress));
    const speed = 0.22;
    particles.forEach((pt, n) => {
      const uv = gridSample(day, pt.lon, pt.lat);
      if (!uv || pt.age++ > 90) { particles[n] = spawn(proj); return; }
      const a = proj([pt.lon, pt.lat]);
      if (!a) { particles[n] = spawn(proj); return; }
      const b = [a[0] + uv[0] * speed, a[1] - uv[1] * speed];
      const ll = proj.invert(b);
      if (!ll) { particles[n] = spawn(proj); return; }
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      pt.lon = ll[0]; pt.lat = ll[1];
    });
    ctx.stroke();
  }
  return { reset, step };
})();

const audio = {
  ctx: null, master: null, gains: {}, analysers: {}, buffers: {}, sources: [],
  playing: false, offset: 0, startAt: 0, pending: {}, revision: 0, levelBuffers: {},
  ensure() {
    if (this.ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = .85;
    this.master.connect(this.ctx.destination);
    STEMS.forEach((s) => {
      const g = this.ctx.createGain();
      const an = this.ctx.createAnalyser();
      an.fftSize = 512;
      g.gain.value = state.layers[s] ? 1 : 0;
      g.connect(an); an.connect(this.master);
      this.gains[s] = g; this.analysers[s] = an;
      this.levelBuffers[s] = new Float32Array(an.fftSize);
    });
  },
  async load(bird) {
    this.ensure();
    if (this.buffers[bird]) return this.buffers[bird];
    if (this.pending[bird]) return this.pending[bird];
    this.pending[bird] = Promise.all(STEMS.map(async (s) => {
      const res = await fetch(`assets/audio/${bird.toLowerCase()}-${s}.mp3`, NO_CACHE);
      if (!res.ok) throw new Error(`${bird} audio could not load`);
      const buffer = await this.ctx.decodeAudioData(await res.arrayBuffer());
      if (buffer.duration < DURATION - .1) throw new Error("Audio and timeline durations do not match");
      return buffer;
    })).then((buffers) => {
      this.buffers[bird] = Object.fromEntries(STEMS.map((s, i) => [s, buffers[i]]));
      delete this.pending[bird];
      return this.buffers[bird];
    }).catch((error) => { delete this.pending[bird]; throw error; });
    return this.pending[bird];
  },
  time() { return this.playing ? clamp(this.ctx.currentTime - this.startAt, 0, DURATION) : this.offset; },
  async play() {
    windDemo.stop();
    const revision = ++this.revision;
    const bird = state.bird;
    const offset = this.offset >= DURATION - .05 ? 0 : this.offset;
    this.ensure();
    await this.ctx.resume();
    toast("Loading sound…", 900);
    const bufs = await this.load(bird);
    if (revision !== this.revision || bird !== state.bird) return;
    const now = this.ctx.currentTime;
    const start = now + .055;
    this.stopSources(now + .045);
    this.master.gain.cancelScheduledValues(now);
    this.master.gain.setValueAtTime(this.master.gain.value, now);
    this.master.gain.linearRampToValueAtTime(0, now + .04);
    this.master.gain.setValueAtTime(0, start);
    this.master.gain.linearRampToValueAtTime(.85, start + .12);
    this.offset = offset;
    this.sources = STEMS.map((s) => {
      const src = this.ctx.createBufferSource();
      src.buffer = bufs[s];
      src.connect(this.gains[s]);
      src.start(start, offset, DURATION - offset);
      return src;
    });
    this.startAt = start - offset;
    this.playing = true;
    setPlayUI(true);
  },
  pause() {
    this.revision += 1;
    if (!this.playing) return;
    this.offset = this.time();
    const now = this.ctx.currentTime;
    this.master.gain.cancelScheduledValues(now);
    this.master.gain.setValueAtTime(this.master.gain.value, now);
    this.master.gain.linearRampToValueAtTime(0, now + .04);
    this.stopSources(now + .045);
    this.playing = false;
    setPlayUI(false);
  },
  seek(t) {
    this.offset = clamp(t, 0, DURATION);
    if (this.playing) this.play().catch(() => toast("The sound could not load, press Play to try again", 3000));
  },
  stopSources(at) { this.sources.forEach((s) => { try { s.stop(at); } catch {} }); this.sources = []; },
  setStem(stem, on) {
    if (!this.ctx) return;
    this.gains[stem].gain.setTargetAtTime(on ? 1 : 0, this.ctx.currentTime, 0.06);
    windDemo.gains[stem]?.gain.setTargetAtTime(on ? .85 : 0, this.ctx.currentTime, .06);
  },
  level(stem) {
    const an = windDemo.playing ? windDemo.analysers[stem] : this.analysers[stem];
    if (!an || (!this.playing && !windDemo.playing)) return 0;
    const buf = this.levelBuffers[stem];
    an.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    return clamp(Math.sqrt(sum / buf.length) * 5, 0, 1);
  },
};

const windDemo = {
  buffers: null, pending: null, sources: [], gains: {}, analysers: {}, active: false, playing: false, offset: 0, startedAt: 0, revision: 0,
  async load() {
    if (!data.score.wind_example) throw new Error("No wind example is available");
    audio.ensure();
    if (this.buffers) return this.buffers;
    if (!this.pending) this.pending = Promise.all(STEMS.map(async (stem) => {
      const response = await fetch(data.score.wind_example.files[stem], NO_CACHE);
      if (!response.ok) throw new Error("The wind sound could not load");
      const buffer = await audio.ctx.decodeAudioData(await response.arrayBuffer());
      if (buffer.duration < 7.9) throw new Error("The wind sound is incomplete");
      return buffer;
    })).then((buffers) => {
      this.buffers = Object.fromEntries(STEMS.map((stem, index) => [stem, buffers[index]]));
      return this.buffers;
    }).finally(() => { this.pending = null; });
    return this.pending;
  },
  time() { return this.playing ? clamp(this.offset + audio.ctx.currentTime - this.startedAt, 0, 8) : this.offset; },
  observedTime() {
    const hours = data.score.wind_example.hours;
    return lerp(Date.parse(hours[0]), Date.parse(hours.at(-1)), this.time() / 8);
  },
  async play(offset = 0) {
    const revision = ++this.revision;
    audio.pause();
    audio.ensure();
    await audio.ctx.resume();
    const buffers = await this.load();
    if (revision !== this.revision || state.bird !== "Nico") return;
    this.pause();
    this.offset = clamp(offset, 0, 7.99);
    this.startedAt = audio.ctx.currentTime + .025;
    this.sources = STEMS.map((stem) => {
      const gain = audio.ctx.createGain(); gain.gain.value = 0;
      const analyser = audio.ctx.createAnalyser(); analyser.fftSize = 512;
      gain.connect(analyser).connect(audio.ctx.destination);
      gain.gain.linearRampToValueAtTime(state.layers[stem] ? .85 : 0, this.startedAt + .12);
      this.gains[stem] = gain; this.analysers[stem] = analyser;
      const source = audio.ctx.createBufferSource(); source.buffer = buffers[stem];
      source.connect(gain); source.start(this.startedAt, this.offset, 8 - this.offset);
      return source;
    });
    this.active = true; this.playing = true;
    setPlayUI(true); updateWindButtons();
  },
  pause() {
    this.revision += 1;
    const wasPlaying = this.playing;
    this.offset = this.time();
    this.sources.forEach((source) => { source.stop(audio.ctx.currentTime + .1); });
    this.sources = [];
    const previous = [...Object.values(this.gains), ...Object.values(this.analysers)];
    Object.values(this.gains).forEach((gain) => gain.gain.setTargetAtTime(0, audio.ctx.currentTime, .025));
    if (previous.length) setTimeout(() => previous.forEach((node) => node.disconnect()), 150);
    this.gains = {}; this.analysers = {};
    this.playing = false;
    if (wasPlaying) setPlayUI(false);
    updateWindButtons();
  },
  stop() { this.pause(); this.active = false; this.offset = 0; },
};

function updateWindButtons() {
  $$("[data-wind-example]").forEach((button) => {
    button.textContent = windDemo.playing ? "Pause the wind" : "Hear the wind on 30 October";
    button.setAttribute("aria-pressed", String(windDemo.playing));
  });
}

async function hearWind(offset = 0, together = false) {
  if (!data.score.wind_example) return;
  audio.pause(); state.preview = false;
  await setBird("Nico");
  state.progress = timeToProgress(Date.parse(data.score.wind_example.hours[0]), "Nico");
  audio.offset = state.progress * JOURNEY;
  state.layers = { movement: together, temperature: together, wind: true };
  syncLayers(); updateMotionUI(); renderDynamic();
  try { await windDemo.play(offset); }
  catch (error) { toast("The wind sound could not load, try again", 3000); console.error(error); }
}

function setPlayUI(on) {
  const btn = $("#btn-play");
  btn.setAttribute("aria-pressed", String(on));
  btn.setAttribute("aria-label", on ? "Pause" : "Play");
  app.dataset.animate = on || state.preview || pitch.running || windDemo.playing ? "on" : "off";
}
async function togglePlay() {
  if (windDemo.active) {
    if (windDemo.playing) windDemo.pause(); else windDemo.play(windDemo.offset >= 7.95 ? 0 : windDemo.offset).catch(() => toast("The wind sound could not load, try again", 3000));
    return;
  }
  state.preview = false;
  updateMotionUI();
  if (audio.playing) audio.pause();
  else {
    try { audio.offset = state.progress * JOURNEY; await audio.play(); }
    catch (err) { console.error(err); toast("The sound could not load, press Play to try again", 3500); }
  }
}

function buildLayers() {
  const list = d3.select("#layer-list").selectAll("button").data(LAYERS).join("button")
    .attr("type", "button").attr("class", "layer")
    .attr("data-layer", (d) => d.id)
    .style("color", (d) => d.color)
    .html((d) => `<span class="layer-ico"><svg viewBox="0 0 24 24">${d.icon}</svg></span>
      <span><b style="color:var(--text)">${d.name}</b><small>${d.voice}</small><span class="meter"><i></i></span></span>`)
    .on("click", (e, d) => toggleLayer(d.id));
  list.filter((d) => d.id === "wind" && !data.hasWind)
    .attr("disabled", true)
    .attr("title", "No wind data is available");
  const gradient = `linear-gradient(90deg, ${data.eic.legend_colors.join(",")})`;
  $("#legend-temp").style.background = gradient;
  $("#map-temp-key").style.background = gradient;
  syncLayers();
}

function toggleLayer(id, force) {
  if (id === "wind" && !data.hasWind) { toast("There is no wind data for this journey", 3000); return; }
  state.layers[id] = force ?? !state.layers[id];
  syncLayers();
}

function syncLayers() {
  LAYERS.forEach(({ id }) => {
    $(`.layer[data-layer="${id}"]`)?.setAttribute("aria-pressed", String(state.layers[id]));
    audio.setStem(id, state.layers[id]);
  });
  app.dataset.eic = state.layers.temperature ? "on" : "off";
  app.dataset.movement = state.layers.movement ? "on" : "off";
  $$(".mapping button[data-solo]").forEach((b) => {
    const id = b.dataset.solo;
    const solo = LAYERS.every((l) => state.layers[l.id] === (l.id === id) || (l.id === "wind" && !data.hasWind));
    b.setAttribute("aria-pressed", String(solo));
  });
  state.frameIndex = -1;
  wind.reset();
  renderDynamic();
}

function solo(id) {
  if (id === "wind" && data.score.wind_example) { hearWind(); return; }
  windDemo.stop();
  LAYERS.forEach((l) => { state.layers[l.id] = l.id === id && !(l.id === "wind" && !data.hasWind); });
  syncLayers();
  if (!audio.playing) togglePlay();
}
function allLayers() {
  LAYERS.forEach((l) => { state.layers[l.id] = !(l.id === "wind" && !data.hasWind); });
  syncLayers();
}

function buildDock() {
  d3.select(".bird-chips").selectAll("button").data(BIRDS).join("button")
    .attr("type", "button").attr("class", "chip")
    .attr("data-bird", (b) => b)
    .style("--c", (b) => COLORS[b])
    .html((b) => `<i></i>${b}`)
    .on("click", (e, b) => setBird(b));
  $("#btn-play").addEventListener("click", togglePlay);
}

const tl = { x: null, w: 0, h: 0 };
function drawTimeline() {
  if (!data.points) return;
  const el = $("#timeline");
  const { width, height } = el.getBoundingClientRect();
  if (!width) return;
  const s = d3.select(el);
  tl.w = width; tl.h = height;
  const m = { l: 4, r: 4, t: 14, b: 16 };
  const x = d3.scaleLinear([0, 1], [m.l, width - m.r]);
  tl.x = x;
  const mid = m.t + (height - m.t - m.b) * 0.62;
  const yUp = d3.scaleLinear([0, data.maxKm], [0, mid - m.t]);
  const pts = data.points[state.bird];
  const bw = Math.max(1, (width - m.l - m.r) / pts.length - 0.6);
  const prog = (d) => timeToProgress(d.t);

  s.selectAll(".tl-label").data(["daily travel · km"]).join("text")
    .attr("class", (d, i) => (i ? "tl-label tl-label-wind" : "tl-label")).attr("x", (d, i) => (i ? m.l : m.l + 12)).attr("y", (d, i) => (i ? height - 2 : 9)).text((d) => d);

  s.selectAll(".tl-base").data([0]).join("line").attr("class", "tl-base")
    .attr("x1", m.l).attr("x2", width - m.r).attr("y1", mid).attr("y2", mid);

  s.selectAll(".tl-bar").data(pts, (d) => d.date).join("rect")
    .attr("class", "tl-bar")
    .attr("x", (d) => x(prog(d)) - bw / 2).attr("width", bw)
    .attr("y", (d) => mid - yUp(d.gap ? 0 : d.km)).attr("height", (d) => yUp(d.gap ? 0 : d.km));

  s.selectAll(".tl-gap").data(pts.filter((d) => d.gap)).join("rect").attr("class", "tl-gap")
    .attr("x", (d) => x(prog(d)) - bw).attr("width", bw * 2).attr("y", m.t).attr("height", height - m.t - m.b);

  const rows = data.wind?.[state.bird] ? pts.map((d) => ({ d, w: data.wind[state.bird][d.date] })).filter((r) => r.w?.tail != null) : [];
  const yDown = d3.scaleLinear([0, data.tailMax || 15], [0, height - m.b - mid - 2]);
  s.selectAll(".tl-tail").data(rows, (r) => r.d.date).join("rect").attr("class", "tl-tail")
    .attr("fill", (r) => (r.w.tail >= 0 ? "#7cc4ff" : "#ff8a3d"))
    .attr("x", (r) => x(prog(r.d)) - bw / 2).attr("width", bw)
    .attr("y", (r) => (r.w.tail >= 0 ? mid + 2 : mid + 2)).attr("height", (r) => yDown(Math.abs(r.w.tail)))
    .attr("opacity", (r) => (r.w.tail >= 0 ? 0.85 : 0.6));

  const [a, b] = birdSpan();
  const months = d3.utcMonth.range(new Date(a), new Date(b));
  s.selectAll(".tl-axis").data([0]).join("g").attr("class", "tl-axis")
    .attr("transform", `translate(0,${height - m.b + 12})`)
    .selectAll("text").data(months).join("text")
    .attr("x", (d) => x(timeToProgress(+d))).attr("text-anchor", "middle")
    .text((d) => d3.utcFormat("%b")(d));

  const head = s.selectAll(".tl-head").data([0]).join((e) => {
    const g = e.append("g").attr("class", "tl-head");
    g.append("line");
    g.append("circle").attr("r", 5);
    return g;
  });
  head.select("line").attr("y1", m.t - 6).attr("y2", height - m.b + 2);
  head.select("circle").attr("cy", m.t - 6);

  s.on("pointerdown", (e) => {
    el.setPointerCapture(e.pointerId);
    const move = (ev) => scrub(x.invert(d3.pointer(ev, el)[0]));
    move(e);
    el.onpointermove = move;
    el.onpointerup = () => { el.onpointermove = null; };
  });
  updatePlayhead();
}

function updatePlayhead() {
  if (!tl.x) return;
  const px = tl.x(state.progress);
  d3.select("#timeline .tl-head").attr("transform", `translate(${px},0)`);
  d3.selectAll("#timeline .tl-bar").classed("is-past", (d) => timeToProgress(d.t) <= state.progress);
  $("#journey-position").value = state.progress * JOURNEY;
  $("#journey-position").setAttribute("aria-valuetext", `${Math.round(state.progress * JOURNEY)} seconds of ${JOURNEY}`);
}

function scrub(p) {
  windDemo.stop();
  state.progress = clamp(p, 0, 1);
  audio.seek(state.progress * JOURNEY);
  renderDynamic();
}

async function setBird(bird) {
  windDemo.stop();
  const wasPlaying = audio.playing;
  audio.pause();
  state.bird = bird;
  app.style.setProperty("--active", COLORS[bird]);
  app.style.setProperty("--active-ink", bird === "Eric" ? "#fff" : "#071018");
  $$(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.textContent.trim() === bird)));
  renderStatic();
  drawTimeline();
  audio.offset = state.progress * JOURNEY;
  if (CHAPTERS[state.chapter].id === "explore") renderStory();
  if (wasPlaying) await togglePlay();
}

const kettle = {
  nodes: null, timer: null, temp: 20,
  colour(t) { const s = d3.scaleLinear([20, 40, 60, 80, 100], ["#3c64d2", "#3cb9e6", "#ebf046", "#fa9628", "#c81e14"]); return s(t); },
  draw(root) {
    const svgK = d3.select(root).select("svg");
    const t = this.temp;
    svgK.select(".water").attr("fill", this.colour(t));
    svgK.selectAll(".steam").attr("opacity", (d, i) => clamp((t - 55 - i * 12) / 25, 0, 1))
      .attr("transform", (d, i) => `translate(0,${-((t / 3 + i * 9) % 18)})`);
    $("#kettle-temp").textContent = `${Math.round(t)}°C`;
    $("#kettle-slider").value = t;
    const waveform = $("#kettle-wave");
    if (waveform) waveform.setAttribute("d", d3.range(211).map((i) => {
      const x = i * 2, phase = reducedMotion ? 0 : performance.now() / 260;
      const y = 32 + Math.sin(x / 420 * Math.PI * 2 * (3 + (t - 20) / 8) - phase) * 16;
      return `${i ? "L" : "M"}${x},${y}`;
    }).join(" "));
    this.nodes?.set(t);
  },
  start() {
    audio.ensure();
    audio.ctx.resume();
    this.nodes = createKettleSound(audio.ctx, audio.ctx.destination);
  },
  stop() {
    this.timer?.stop(); this.timer = null;
    if (this.nodes) { this.nodes.stop(); this.nodes = null; }
    $("#kettle-play")?.setAttribute("aria-pressed", "false");
  },
  boil(root) {
    if (this.nodes) { this.stop(); return; }
    this.start();
    $("#kettle-play").setAttribute("aria-pressed", "true");
    const from = this.temp >= 99 ? 20 : this.temp;
    this.timer = d3.timer((ms) => {
      this.temp = clamp(from + (ms / 6000) * (100 - from), 20, 100);
      this.draw(root);
      if (ms > 6000 + 2500) this.stop();
    });
  },
  mount(root) {
    this.temp = 20;
    const s = d3.select(root).select("svg");
    s.attr("viewBox", "0 0 96 120");
    s.append("path").attr("d", "M18 46 Q18 36 30 36 H66 Q78 36 78 46 V100 Q78 110 68 110 H28 Q18 110 18 100Z").attr("fill", "rgba(255,255,255,.06)").attr("stroke", "rgba(255,255,255,.5)").attr("stroke-width", 1.5);
    s.append("path").attr("class", "water").attr("d", "M21 60 H75 V99 Q75 107 67 107 H29 Q21 107 21 99Z");
    s.append("path").attr("d", "M78 52 L92 40").attr("stroke", "rgba(255,255,255,.5)").attr("stroke-width", 4).attr("stroke-linecap", "round");
    s.append("path").attr("d", "M34 36 Q48 22 62 36").attr("fill", "none").attr("stroke", "rgba(255,255,255,.5)").attr("stroke-width", 2);
    s.selectAll(".steam").data([0, 1, 2]).join("path").attr("class", "steam")
      .attr("d", (d) => `M${88 + d * 3} ${30 - d * 2} q-4 -6 0 -12 q4 -6 0 -12`).attr("fill", "none").attr("stroke", "rgba(255,255,255,.7)").attr("stroke-width", 1.6).attr("stroke-linecap", "round");
    $("#kettle-slider").addEventListener("input", (e) => {
      this.temp = +e.target.value;
      if (!this.nodes) { this.start(); $("#kettle-play").setAttribute("aria-pressed", "true"); }
      this.timer?.stop(); this.timer = null;
      this.draw(root);
    });
    $("#kettle-play").addEventListener("click", () => this.boil(root));
    this.draw(root);
  },
};

const CHAPTERS = [
  {
    id: "hook", layout: "hero", dock: false, layers: false, mood: "plain",
    html: () => `
      <p class="kicker">NASA Space Apps Challenge 2026</p>
      <h2 class="h-question">Have you ever wondered what it <em style="font-style:normal;color:var(--sanne)">feels</em> like to be a migrating bird?</h2>
      <p class="lede">Eric, Nico and Sanne nest in the same colony on the Belgian coast. In autumn 2024 they flew south on different routes.</p>`,
    enter: () => drawRoutesIn(),
  },
  {
    id: "maps", layout: "card", dock: false, layers: false, mood: "mono", all: true,
    html: () => `
      <p class="kicker">The journey</p>
      <h2>Every journey has a rhythm and a story</h2>
      <p class="lede">Thousands of kilometres over the sea, riding the wind. Then a long rest, while the temperature shifts and the wind changes around them.</p>`,
  },
  {
    id: "sound", layout: "card", dock: false, layers: false, mood: "dim",
    html: () => `
      <p class="kicker">An everyday example</p>
      <h2>Listening to a kettle</h2>
      <p class="lede">You can tell the water is boiling from the sound, without checking a thermometer.</p>
      <div class="kettle" id="kettle">
        <svg aria-hidden="true"></svg>
        <div class="kettle-controls">
          <label for="kettle-slider">Water temperature <output id="kettle-temp">20°C</output></label>
          <input id="kettle-slider" type="range" min="20" max="100" step="1" value="20">
          <button type="button" class="ghost" id="kettle-play" aria-pressed="false">Boil the kettle</button>
        </div>
      </div><svg class="kettle-wave" viewBox="0 0 420 64" role="img" aria-label="A wave becomes more closely spaced as the pitch rises"><path id="kettle-wave" fill="none" stroke="var(--sanne)" stroke-width="2"/></svg>`,
    enter: (root) => kettle.mount(root.querySelector("#kettle")),
    leave: () => kettle.stop(),
  },
  {
    id: "invite", layout: "card", dock: false, layers: false, mood: "normal",
    html: () => `
      <p class="kicker">Flock Around and Find Out</p>
      <h2>Let's hear what the journey feels like</h2>
      <p class="lede">Our project turns each gull's GPS track and NASA Earth imagery into sound, so you can follow the flight, the rests and the world around them by ear.</p>`,
  },
  {
    id: "how", layout: "card", dock: true, layers: false, mood: "normal",
    html: () => `
      <p class="kicker">How it works</p>
      <h2>What each sound means</h2>
      <p class="lede">Play them one at a time, then all together.</p>
      <ul class="mapping">
        <li><button type="button" data-solo="movement"><i style="background:#fff"></i><span><b>Bird movement</b><small>Where the bird is and how far it flew</small></span><em>Melody</em></button></li>
        <li><button type="button" data-solo="temperature"><i style="background:#ff8a3d"></i><span><b>NASA temperature image</b><small>Colour under the bird</small></span><em>Chords</em></button></li>
        <li><button type="button" data-solo="wind" ${data.hasWind ? "" : "disabled"}><i style="background:#7cc4ff"></i><span><b>Wind near Nico</b><small>Recorded on 30 October 2024</small></span><em>Wind</em></button></li>
      </ul>
      <p class="hint"><button type="button" class="ghost" data-all>Play all three</button></p>`,
    enter: (root) => {
      root.querySelectorAll("[data-solo]").forEach((b) => b.addEventListener("click", () => solo(b.dataset.solo)));
      root.querySelector("[data-all]").addEventListener("click", () => {
        if (data.score.wind_example) hearWind(0, true);
        else { allLayers(); if (!audio.playing) togglePlay(); }
      });
      syncLayers();
    },
  },
  {
    id: "explore", layout: "card", dock: true, layers: true, mood: "normal",
    html: () => {
      const t = data.totals[state.bird];
      const p = BIRD_INFO[state.bird];
      return `
      <p class="kicker">Listen</p>
      <h2>Pick a bird and press play</h2>
      <p class="lede">${p.line}</p>
      <dl class="bird-stats">
        <div><dt>Tracked</dt><dd>${fmtInt(t.total_km)} km</dd></div>
        <div><dt>Stopovers</dt><dd>${t.n_stopovers}</dd></div>
        <div><dt>Winter</dt><dd>${p.winter}</dd></div>
      </dl>
`;
    },
  },
  {
    id: "future", layout: "wide", dock: false, layers: false, mood: "normal",
    html: () => `
      <p class="kicker">What's next</p>
      <h2>Other data we could play</h2>
      <div class="cards">
        <div class="card"><b>Wildfires</b><span>Fire detections from NASA satellites</span></div>
        <div class="card"><b>Rainfall</b><span>A storm as it builds and passes</span></div>
        <div class="card"><b>Spacecraft</b><span>An orbit or a flyby as a melody</span></div>
      </div>
      <div class="visit">
        <img class="qr" src="assets/iwaktempe.png" width="1147" height="1147" alt="QR code linking to danilyanedo7.github.io/rongawi-submission-nasa">
        <div><b>Fly along yourself</b><span>Scan to open Flock Around and Find Out on your phone</span><span class="url">danilyanedo7.github.io/rongawi-submission-nasa</span></div>
      </div>
`,
  },
];

function drawRoutesIn() {
  if (reducedMotion) return;
  setTimeout(() => {
    svg.selectAll(".route").each(function (b, i) {
      const len = this.getTotalLength();
      d3.select(this).attr("stroke-dasharray", `${len} ${len}`).attr("stroke-dashoffset", len)
        .transition().delay(i * 350).duration(2600).ease(d3.easeCubicInOut).attr("stroke-dashoffset", 0)
        .on("end", function () { d3.select(this).attr("stroke-dasharray", null).attr("stroke-dashoffset", null); });
    });
  }, 300);
}

function renderStory() {
  const ch = CHAPTERS[state.chapter];
  const root = $("#story");
  root.innerHTML = ch.html();
  root.querySelector("[data-next]")?.addEventListener("click", () => go(state.chapter + 1));
  root.querySelectorAll("[data-wind-example]").forEach((button) => button.addEventListener("click", () => {
    const wasPlaying = windDemo.playing;
    const offset = windDemo.active && windDemo.offset < 7.95 ? windDemo.offset : 0;
    if (pitch.running) pausePitch();
    if (wasPlaying) windDemo.pause(); else hearWind(offset);
  }));
  ch.enter?.(root);
  updateWindButtons();
}

const CHAPTER_SECONDS = [15, 17, 18, 12, 22, 26, 10];
const PITCH_STARTS = CHAPTER_SECONDS.map((_, index) => d3.sum(CHAPTER_SECONDS.slice(0, index)));
const PITCH_TITLES = ["Have you ever wondered what it feels like to be a migrating bird?", "Every journey has a rhythm and a story", "Listening to a kettle", "Let's hear what the journey feels like", "What each sound means", "Pick a bird and press play", "Other data we could play"];
const PITCH_SCRIPTS = [
  "These are three gulls from the same colony in Zeebrugge, Belgium. We call them Eric, Nico and Sanne. In autumn 2024 they flew south for the winter, each on a different route.",
  "This is the usual way to show a migration, as lines on a map. That works if you can see it. We wanted to know what the same data sounds like.",
  "You don't need a thermometer to know the kettle is boiling. The hiss gets louder and then the whistle starts. That is the idea behind the project: hearing a change instead of reading it.",
  "With sound, someone can follow the birds without seeing the screen. It also makes changes over time easy to notice, like a bird speeding up or stopping to rest.",
  "Here are the three sounds. The beat comes from how far the bird flew each day. The notes come from the colour of the NASA temperature image under the bird. For one day, 30 October, we added the wind Nico had while crossing the Bay of Biscay. First one at a time, then together.",
  "Now the full journeys. Eric spent the winter in Spain. Nico and Sanne went further, to Dakhla on the Saharan coast, and Sanne was the last to leave.",
  "The same method could work for other NASA data, such as fire detections, rainfall or a spacecraft's path. We haven't built those yet."
];
const pitch = { running: false, elapsed: 0, startedAt: 0, cue: "", preparing: false, revision: 0 };
function pitchTime() { return clamp(pitch.elapsed + (pitch.running ? (performance.now() - pitch.startedAt) / 1000 : 0), 0, 120); }
function updatePitchUI() {
  const seconds = pitchTime();
  const formatted = `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  $("#pt-time").textContent = formatted;
  $("#pt-fill").style.width = `${seconds / 120 * 100}%`;
  const pitchButton = $("#btn-pitch");
  if (pitchButton) {
    pitchButton.textContent = pitch.running ? "Pause pitch" : seconds >= 120 ? "Replay pitch" : seconds > 0 ? "Resume pitch" : "Start pitch";
    pitchButton.setAttribute("aria-pressed", String(pitch.running));
  }
  $("#pv-time").textContent = formatted;
  $("#pv-chapter").textContent = `${state.chapter + 1} / ${CHAPTERS.length}`;
  $("#pv-seconds").textContent = `${CHAPTER_SECONDS[state.chapter]} seconds`;
  $("#pv-title").textContent = PITCH_TITLES[state.chapter];
  $("#pv-script").textContent = PITCH_SCRIPTS[state.chapter];
  $("#pv-cue").textContent = state.chapter === 4 ? "Listen to movement, the temperature image, the wind, then the sounds together" : state.chapter === 5 ? "Leave time to hear Eric, Nico, then Sanne" : "Follow the picture on screen";
  $("#pv-next").textContent = PITCH_TITLES[state.chapter + 1] || "End of pitch";
  $("#pv-prev").disabled = state.chapter === 0;
  $("#pv-go").disabled = state.chapter === CHAPTERS.length - 1;
}
function pausePitch() {
  pitch.elapsed = pitchTime(); pitch.running = false;
  audio.pause(); windDemo.pause(); kettle.stop(); updatePitchUI();
}
async function startPitch() {
  if (pitch.preparing) return;
  if (pitch.running) { pausePitch(); return; }
  if (pitch.elapsed >= 120) { pitch.elapsed = 0; go(0, false); }
  const revision = ++pitch.revision;
  pitch.preparing = true; if ($("#btn-pitch")) $("#btn-pitch").disabled = true;
  try {
    windDemo.stop(); audio.ensure(); await audio.ctx.resume();
    await Promise.all([...BIRDS.map((bird) => audio.load(bird)), ...(data.score.wind_example ? [windDemo.load()] : [])]);
    if (revision !== pitch.revision) return;
    audio.pause(); kettle.stop(); state.preview = false;
    if (pitch.elapsed === 0) { state.progress = 0; await setBird("Eric"); go(0, false); }
    pitch.running = true; pitch.startedAt = performance.now(); pitch.cue = "";
    updateMotionUI(); updatePitchUI();
  } catch (error) { toast("The sound could not load, press P to try again", 3500); console.error(error); }
  finally { pitch.preparing = false; if ($("#btn-pitch")) $("#btn-pitch").disabled = false; }
}
function resetPitch() {
  pitch.revision += 1; pitch.running = false; pitch.elapsed = 0; pitch.cue = "";
  audio.pause(); windDemo.stop(); kettle.stop(); state.progress = 0; audio.offset = 0;
  go(0, false); renderDynamic(); updatePitchUI();
}
function updateMotionUI() {
  const button = $("#btn-motion");
  button.setAttribute("aria-pressed", String(state.preview));
  button.setAttribute("aria-label", state.preview ? "Pause map animation" : "Animate map");
  button.title = state.preview ? "Pause map animation" : "Animate map";
  button.querySelector("path").setAttribute("d", state.preview ? "M8 5v14M16 5v14" : "M8 5v14l11-7z");
  app.dataset.animate = state.preview || audio.playing || pitch.running || windDemo.playing ? "on" : "off";
}
function tickPitch() {
  const seconds = pitchTime();
  const chapter = Math.max(0, PITCH_STARTS.findLastIndex((start) => seconds >= start));
  if (chapter !== state.chapter) go(chapter, true, true);
  const local = seconds - PITCH_STARTS[chapter];
  let cue = `${chapter}`;
  if (chapter === 2) cue += local < 7 ? ":tone" : ":quiet";
  if (chapter === 4) cue += local < 5 ? ":movement" : local < 10 ? ":temperature" : local < 16 && data.score.wind_example ? ":wind" : ":all";
  if (chapter === 5) cue += `:${local < 8 ? 0 : local < 17 ? 1 : 2}`;
  if (cue !== pitch.cue) {
    pitch.cue = cue;
    if (chapter === 4) {
      const stem = cue.split(":")[1];
      if (stem === "wind") { hearWind(local - 10); }
      else if (stem === "all" && data.score.wind_example) { hearWind(local - 16, true); }
      else {
      LAYERS.forEach((layer) => { state.layers[layer.id] = (stem === "all" || layer.id === stem) && (layer.id !== "wind" || data.hasWind); });
      audio.pause(); state.bird = "Eric"; setBirdSilently("Eric");
      state.progress = local / JOURNEY; audio.offset = local;
      drawTimeline(); syncLayers(); audio.play().catch(() => toast("The sound could not load, press Play to try again", 2500));
      }
    } else if (chapter === 5) {
      const birdIndex = Number(cue.split(":")[1]);
      audio.pause(); setBird(BIRDS[birdIndex]); allLayers();
      const offset = JOURNEY * .7 + local - [0, 8, 17][birdIndex];
      state.progress = Math.min(offset / JOURNEY, 1); audio.offset = offset;
      audio.play().catch(() => toast("The sound could not load, press Play to try again", 2500));
    } else {
      audio.pause();
      if (cue === "2:tone") kettle.start(); else kettle.stop();
    }
  }
  if (cue === "2:tone") { kettle.temp = 20 + clamp(local / 6, 0, 1) * 80; kettle.draw($("#kettle")); }
  if (!audio.playing && [0, 1, 6].includes(chapter)) { state.progress = clamp(local / CHAPTER_SECONDS[chapter], 0, 1); renderDynamic(); }
  if (seconds >= 120) { pitch.elapsed = 120; pitch.running = false; audio.pause(); windDemo.stop(); kettle.stop(); }
  updatePitchUI();
}

function go(index, animate = true, fromTimer = false) {
  index = clamp(index, 0, CHAPTERS.length - 1);
  if (index !== state.chapter || !fromTimer) windDemo.stop();
  if (!fromTimer) { pitch.elapsed = PITCH_STARTS[index]; pitch.startedAt = performance.now(); pitch.cue = ""; }
  const prev = CHAPTERS[state.chapter];
  if (index !== state.chapter) prev.leave?.();
  state.chapter = index;
  const ch = CHAPTERS[index];

  const story = $(".story");
  story.classList.toggle("is-hero", ch.layout === "hero");
  story.classList.toggle("is-wide", ch.layout === "wide");
  $(".dock").classList.toggle("is-hidden", !ch.dock);
  $(".layers").classList.toggle("is-hidden", !ch.layers);
  app.dataset.dock = ch.dock ? "on" : "off";
  app.dataset.mood = ch.mood;
  app.dataset.all = ch.all ? "on" : "off";
  app.dataset.chapter = String(index);
  app.dataset.wind = ch.id === "how" && data.hasWind ? "on" : "off";
  $("#temperature-key").classList.toggle("is-off", ch.layers || ["plain", "mono", "dim"].includes(ch.mood));
  if (!ch.dock && audio.playing) audio.pause();
  if (!ch.layers || ch.id === "explore") allLayers();

  renderStory();
  updateRouteClasses();
  $("#story-count").textContent = `${index + 1} / ${CHAPTERS.length}`;
  $("#btn-prev").disabled = index === 0;
  $("#btn-next").disabled = index === CHAPTERS.length - 1;
  $$(".chapter-dots button").forEach((b, i) => {
    if (i === index) b.setAttribute("aria-current", "step");
    else b.removeAttribute("aria-current");
  });
  history.replaceState(null, "", `#${index + 1}`);
  $("#mobile-chapter").value = index;
  updatePitchUI();
  requestAnimationFrame(() => { drawTimeline(); flyTo(animate); });
}

function buildNav() {
  d3.select(".chapter-dots").selectAll("button").data(CHAPTERS).join("button")
    .attr("type", "button").attr("aria-label", (d, i) => `Chapter ${i + 1}`)
    .on("click", (e, d) => go(CHAPTERS.indexOf(d)));
  d3.select("#mobile-chapter").selectAll("option").data(CHAPTERS).join("option").attr("value", (_, i) => i).text((_, i) => `${i + 1}. ${PITCH_TITLES[i]}`);
  $("#mobile-chapter").addEventListener("change", (event) => go(Number(event.target.value)));
  $("#journey-position").max = JOURNEY;
  $("#journey-position").addEventListener("input", (event) => { state.preview = false; updateMotionUI(); scrub(Number(event.target.value) / JOURNEY); });
  $("#btn-pitch")?.addEventListener("click", startPitch);
  $("#btn-motion").addEventListener("click", () => {
    if (pitch.running) pausePitch();
    audio.pause(); windDemo.stop(); state.preview = !state.preview;
    if (state.preview && state.progress >= 1) state.progress = 0;
    updateMotionUI(); renderDynamic();
  });
  $("#btn-presenter")?.addEventListener("click", () => { updatePitchUI(); $("#presenter").showModal(); });
  $("#pv-close").addEventListener("click", () => $("#presenter").close());
  $("#pv-prev").addEventListener("click", () => go(state.chapter - 1));
  $("#pv-go").addEventListener("click", () => go(state.chapter + 1));
  $("#pv-reset").addEventListener("click", resetPitch);
  $("#btn-prev").addEventListener("click", () => go(state.chapter - 1));
  $("#btn-next").addEventListener("click", () => go(state.chapter + 1));
  $("#btn-fullscreen").addEventListener("click", toggleFullscreen);
  $("#btn-fullscreen").hidden = !document.documentElement.requestFullscreen;

  window.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.target.closest("input,textarea,select,button,a,dialog")) return;
    const ch = CHAPTERS[state.chapter];
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(state.chapter + 1); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(state.chapter - 1); }
    else if (e.key === "f" || e.key === "F") toggleFullscreen();
    else if (e.key.toLowerCase() === "p") startPitch();
    else if (e.key.toLowerCase() === "r") resetPitch();
    else if (e.key.toLowerCase() === "o") $("#presenter").showModal();
    else if (e.key === " " && ch.dock) { e.preventDefault(); togglePlay(); }
    else if (["1", "2", "3"].includes(e.key) && ch.dock) setBird(BIRDS[+e.key - 1]);
    else if ((e.key === "t" || e.key === "T") && ch.layers) toggleLayer("temperature");
    else if ((e.key === "w" || e.key === "W") && ch.layers) toggleLayer("wind");
    else if ((e.key === "m" || e.key === "M") && ch.layers) toggleLayer("movement");
  });

  let touchX = null;
  $(".story").addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
  $(".story").addEventListener("touchend", (e) => {
    if (touchX === null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 60) go(state.chapter + (dx < 0 ? 1 : -1));
    touchX = null;
  });
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => {});
}

let toastTimer;
function toast(msg, ms = 2000) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("is-on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("is-on"), ms);
}

// wings run on a continuous phase, so a new flap speed never restarts the beat
function flapWings(delta) {
  const moving = app.dataset.animate === "on" && !reducedMotion;
  const ease = 1 - Math.exp(-delta * 4);
  [activeGull, ...Object.values(otherGulls)].forEach((gull) => {
    gull.rate += (gull.targetRate - gull.rate) * ease;
    gull.fold += (gull.targetFold - gull.fold) * ease;
    if (moving) gull.phase = (gull.phase + delta * gull.rate) % 1;
    const s = (1 - Math.cos(2 * Math.PI * gull.phase)) / 2;
    const sx = (1.04 - .32 * s) * (1 - gull.fold) + .34 * gull.fold;
    const sy = (.96 + .18 * s) * (1 - gull.fold) + 1.25 * gull.fold;
    const transform = `scaleX(${sx.toFixed(3)}) scaleY(${sy.toFixed(3)})`;
    gull.wings.forEach((wing) => { wing.style.transform = transform; });
  });
}

let lastTick = performance.now();
function tick() {
  const now = performance.now();
  const delta = Math.min((now - lastTick) / 1000, .1); lastTick = now;
  flapWings(delta);
  if (pitch.running) tickPitch();
  if (windDemo.playing) {
    state.progress = timeToProgress(windDemo.observedTime(), "Nico");
    renderDynamic();
    if (windDemo.time() >= 8) windDemo.pause();
  }
  if (audio.playing) {
    const t = audio.time();
    if (t >= DURATION) {
      audio.pause();
      audio.offset = 0;
      state.progress = 1;
    } else {
      state.progress = Math.min(t / JOURNEY, 1);
    }
    renderDynamic();
  } else if (state.preview && ["hook", "maps"].includes(CHAPTERS[state.chapter].id)) {
    state.progress = clamp(state.progress + delta / JOURNEY, 0, 1);
    renderDynamic();
    if (state.progress >= 1) { state.preview = false; updateMotionUI(); }
  }
  STEMS.forEach((s) => {
    const meter = $(`.layer[data-layer="${s}"] .meter i`);
    if (meter) meter.style.transform = `scaleX(${audio.level(s)})`;
  });
  const pulse = svg.select(".dot-pulse");
  if (!pulse.empty() && !reducedMotion && (audio.playing || state.preview || pitch.running)) {
    const ph = (performance.now() % 1600) / 1600;
    pulse.attr("r", 8 + ph * 16).attr("opacity", 0.8 * (1 - ph));
  }
  wind.step();
  requestAnimationFrame(tick);
}

async function init() {
  try {
    await loadData();
  } catch (err) {
    console.error(err);
    $$("#app button, #app select, #app input").forEach((control) => { control.disabled = true; });
    $("#story").innerHTML = `<h2>The journeys could not load</h2><p class="lede">Try loading the page again</p><button type="button" class="cta" id="retry-data">Reload journeys</button>`;
    $("#retry-data").addEventListener("click", () => location.reload());
    return;
  }
  buildNav();
  buildLayers();
  buildDock();
  updateMotionUI();
  const fromHash = parseInt(location.hash.slice(1), 10);
  state.chapter = Number.isFinite(fromHash) ? clamp(fromHash - 1, 0, CHAPTERS.length - 1) : 0;
  setBirdSilently("Eric");
  resize();
  go(state.chapter, false);
  window.addEventListener("resize", () => { clearTimeout(init.rt); init.rt = setTimeout(resize, 120); });
  requestAnimationFrame(tick);
}

function setBirdSilently(bird) {
  state.bird = bird;
  app.style.setProperty("--active", COLORS[bird]);
  app.style.setProperty("--active-ink", bird === "Eric" ? "#fff" : "#071018");
  $$(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.textContent.trim() === bird)));
}

init();
})();
