/* documentation page: route map, scrollytelling focus and section highlighting */
(() => {
  const COLORS = { Eric: "#a8201a", Nico: "#0a9396", Sanne: "#ec9a29" };
  const NASA_RAMP = [[235, 228, 250], [205, 168, 200], [160, 100, 210], [60, 50, 200], [40, 90, 215], [30, 130, 230], [60, 185, 230], [130, 205, 210],
    [110, 225, 140], [235, 240, 70], [250, 175, 40], [235, 80, 25], [175, 25, 15], [95, 15, 10], [150, 140, 140], [205, 200, 200]];

  const ramp = document.getElementById("ramp-nasa");
  if (ramp) ramp.innerHTML = NASA_RAMP.map(([r, g, b]) => `<li style="--c:rgb(${r},${g},${b})"></li>`).join("");

  async function drawRoutes() {
    const host = document.getElementById("route-map");
    if (!host || !window.d3) return;
    const [daily, totals, world] = await Promise.all(["daily", "totals", "countries-50m"].map((f) => d3.json(`assets/data/${f}.json`)));
    const span = Object.fromEntries(totals.map((t) => [t.bird, t]));
    const routes = Object.keys(COLORS).map((bird) => ({
      bird,
      points: daily.filter((p) => p.bird === bird && p.date >= span[bird].departure_date && p.date <= span[bird].return_date).map((p) => [p.lon, p.lat]),
    }));
    const width = 520, height = 560;
    const shape = { type: "MultiPoint", coordinates: routes.flatMap((r) => r.points) };
    const projection = d3.geoMercator().fitExtent([[40, 40], [width - 40, height - 30]], shape);
    const path = d3.geoPath(projection);
    const svg = d3.select(host).append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("aria-hidden", "true");
    svg.append("path").attr("class", "land").attr("d", path(topojson.feature(world, world.objects.countries)));
    svg.selectAll(".route").data(routes).join("path")
      .attr("class", (d) => `route ${d.bird}`).attr("stroke", (d) => COLORS[d.bird])
      .attr("d", (d) => path({ type: "LineString", coordinates: d.points }));
    const places = [["Zeebrugge", 3.2, 51.33, -8, "end"], ["Dakhla", -15.96, 23.68, -8, "end"], ["Spain", -4.7, 39.5, 8, "start"]];
    svg.selectAll(".place").data(places).join("text").attr("class", "place")
      .attr("x", (d) => projection([d[1], d[2]])[0] + d[3]).attr("y", (d) => projection([d[1], d[2]])[1] + 4)
      .attr("text-anchor", (d) => d[4]).text((d) => d[0]);
  }

  function scrolly() {
    if (!("IntersectionObserver" in window)) { document.documentElement.classList.add("no-js"); return; }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const step = entry.target, key = step.dataset.step, scope = step.closest(".scrolly");
        scope.querySelectorAll(".step").forEach((s) => s.classList.toggle("is-active", s === step));
        const map = scope.querySelector(".route-map");
        if (map) map.dataset.focus = key;
        scope.querySelectorAll("[data-for]").forEach((el) => el.classList.toggle("is-on", el.dataset.for === key));
      });
    }, { rootMargin: "-45% 0px -45% 0px" });
    document.querySelectorAll(".step").forEach((s) => io.observe(s));
    document.querySelectorAll(".scrolly").forEach((scope) => {
      const first = scope.querySelector(".step");
      first?.classList.add("is-active");
      scope.querySelector(`[data-for="${first?.dataset.step}"]`)?.classList.add("is-on");
    });
  }

  function sectionMenu() {
    const links = [...document.querySelectorAll(".toc a")];
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        links.forEach((a) => a.toggleAttribute("aria-current", a.getAttribute("href") === `#${entry.target.id}`));
        links.forEach((a) => { if (a.hasAttribute("aria-current")) a.setAttribute("aria-current", "true"); });
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    document.querySelectorAll("main > section[id]").forEach((s) => io.observe(s));
  }

  scrolly();
  sectionMenu();
  drawRoutes().catch(() => {});
})();
