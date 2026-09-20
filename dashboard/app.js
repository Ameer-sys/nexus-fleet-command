const SVG_NS = "http://www.w3.org/2000/svg";
let latest = null;
let socket = null;
let pollTimer = null;
let toastTimer = null;
const selectedPackageIds = new Set();
let selectedDestinationId = null;
let selectedDecisionJobId = null;
let lastBatchResult = null;
let selectedRobotId = null;
let currentContext = "command";
let sidebarCollapsed = true;
let lastFocusEventKey = null;
let focusEventsReady = false;
let hoveredPackageId = null;
let lastTrafficAlertKey = null;
let trafficAlertTimer = null;
let trafficAlertExpandedUntil = 0;
let trafficIdleResetTimer = null;

const $ = (id) => document.getElementById(id);
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
const icon = (name) => `<svg class="ui-icon"><use href="/static/nexus-icons.svg#i-${name}"/></svg>`;

function applyTheme(theme) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = normalized;
  localStorage.setItem("nexus-theme", normalized);
  const dark = normalized === "dark";
  $("theme-toggle").setAttribute("aria-pressed", String(dark));
  $("theme-toggle").setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} mode`);
  $("theme-toggle").querySelector("b").textContent = dark ? "DARK" : "LIGHT";
  $("theme-color").content = dark ? "#07111b" : "#edf3f7";
}

function setButtonLabel(id, iconName, label) {
  $(id).innerHTML = `${icon(iconName)}${esc(label)}`;
}

function point(position) {
  const x = 80 + clamp(position[0], 0, 2) / 2 * 840;
  const y = 610 - clamp(position[1], 0, 2) / 2 * 540;
  return [x, y];
}

function svgElement(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function stateClass(state) { return String(state || "").toLowerCase(); }

function robotColor(robot) {
  if (!robot.available || robot.failed) return "#d94452";
  if (robot.yielding) return "#d98b19";
  if (robot.busy) return "#1677d2";
  return "#159a7a";
}

function packageLabel(item) {
  if (item.package_id === "MEDICAL-CRITICAL") return "CRIT";
  if (item.package_id.startsWith("SERVER")) return "SRV";
  if (item.package_id.startsWith("PRECISION")) return "PREC";
  if (item.package_id.startsWith("SENSOR")) return "SNSR";
  if (item.package_id.startsWith("SUPPLY")) return "SUP";
  if (item.package_id.startsWith("PART")) return "PART";
  if (item.package_id.startsWith("GLASS")) return "GLASS";
  if (item.category === "HIGH_VALUE") return "HV";
  if (item.category === "MEDICAL") return "MED";
  if (item.category === "FRAGILE") return "FRG";
  return "BOX";
}

function packageState(item, job) {
  if (item.status === "QUEUED") return `QUEUED${job?.queue_position ? ` #${job.queue_position}` : ""}`;
  if (item.status === "TO_PICKUP") return `${item.current_custodian || "—"} ASSIGNED`;
  if (item.status === "IN_TRANSIT") return `${item.current_custodian || "—"} IN TRANSIT`;
  if (item.status === "RECOVERY_PENDING") return "⚠ RECOVERY";
  if (item.status === "DELIVERED") return "✓ DELIVERED";
  return item.status;
}

function updateMapTooltip(item, stateText) {
  const tooltip = $("map-tooltip");
  if (!item) { tooltip.classList.add("hidden"); return; }
  tooltip.innerHTML = `<b>${esc(item.name)}</b><span>${esc(item.category.replace("_", " "))} · Priority ${esc(item.priority)}</span><em>${esc(stateText)}</em>`;
}

const contextTitles = {
  command: "FLEET COMMAND",
  robot: "ROBOT DETAILS",
  decision: "NEXUS DECISION",
  tasks: "FLEET TASKS",
  inventory: "WAREHOUSE INVENTORY",
  custody: "VERIFIED CUSTODY",
};

function setSidebarCollapsed(collapsed) {
  sidebarCollapsed = collapsed;
  document.querySelector(".dashboard-grid").classList.toggle("sidebar-collapsed", collapsed);
  $("context-toggle-btn").textContent = collapsed ? "CONTEXT ▶" : "CONTEXT ◀";
}

function setContext(context, open = true) {
  currentContext = context;
  $("context-title").textContent = contextTitles[context] || "NEXUS CONTEXT";
  document.querySelectorAll(".context-view").forEach((view) => view.classList.toggle("active", view.dataset.view === context));
  document.querySelectorAll(".context-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.context === context));
  document.querySelectorAll(".nav-drawer-button[data-nav-context]").forEach((tab) => tab.classList.toggle("active", tab.dataset.navContext === context));
  if (open) setSidebarCollapsed(false);
  setNavDrawer(false);
}

function setNavDrawer(open) {
  const drawer = $("nav-drawer");
  drawer.classList.toggle("open", open);
  drawer.setAttribute("aria-hidden", String(!open));
  $("nav-menu-btn").setAttribute("aria-expanded", String(open));
}

function setDemoTools(open) {
  const menu = $("demo-tools-menu");
  menu.classList.toggle("open", open);
  menu.setAttribute("aria-hidden", String(!open));
  $("demo-tools-btn").setAttribute("aria-expanded", String(open));
}

function toggleFocus(force) {
  const enabled = typeof force === "boolean" ? force : !document.body.classList.contains("focus-mode");
  document.body.classList.toggle("focus-mode", enabled);
  $("focus-btn").setAttribute("aria-label", enabled ? "Exit warehouse focus" : "Expand warehouse");
  $("focus-btn").title = enabled ? "Exit warehouse focus" : "Expand warehouse";
  setDemoTools(false);
  setNavDrawer(false);
  if (!enabled) setSidebarCollapsed(sidebarCollapsed);
}

function renderMap(data) {
  const navigationLayer = $("navigation-layer"), destinationLayer = $("destination-layer"), packageLayer = $("package-layer"), jobLayer = $("job-layer"), routeLayer = $("route-layer"), conflictLayer = $("conflict-layer"), robotLayer = $("robot-layer");
  [navigationLayer, destinationLayer, packageLayer, jobLayer, routeLayer, conflictLayer, robotLayer].forEach((layer) => layer.replaceChildren());

  const navigation = data.warehouse?.navigation;
  (navigation?.edges || []).forEach(([start, end]) => {
    const [x1, y1] = point(start), [x2, y2] = point(end);
    navigationLayer.append(svgElement("line", {x1, y1, x2, y2, class: "aisle-path"}));
  });
  (navigation?.intersections || []).forEach((intersection) => {
    const [x, y] = point(intersection.position);
    navigationLayer.append(svgElement("circle", {cx: x, cy: y, r: 25, class: "intersection-zone"}));
    const label = svgElement("text", {x, y: y - 32, class: "intersection-label", "text-anchor": "middle"});
    label.textContent = "CONTROLLED CROSSING";
    navigationLayer.append(label);
  });
  (navigation?.stop_lines || []).forEach(([start, end]) => {
    const [x1, y1] = point(start), [x2, y2] = point(end);
    navigationLayer.append(svgElement("line", {x1, y1, x2, y2, class: "stop-line"}));
  });

  (data.destinations || []).forEach((destination) => {
    const [x, y] = point(destination.position);
    const selected = destination.destination_id === selectedDestinationId;
    const group = svgElement("g", {class: `destination-node ${selected ? "selected" : ""}`, transform: `translate(${x} ${y})`, tabindex: 0, role: "button", "aria-label": `Deliver to ${destination.name}`});
    group.append(svgElement("rect", {x: -48, y: -18, width: 96, height: 36, fill: selected ? "#d8f4f1" : "#ffffff", stroke: selected ? "#0d9488" : "#8bb4c1", "stroke-width": selected ? 3 : 2}));
    const cap = svgElement("rect", {x: -48, y: -18, width: 96, height: 7, fill: selected ? "#0d9488" : "#b9d4dc"}); group.append(cap);
    const label = svgElement("text", {x: 0, y: 7, fill: "#193746", "font-size": 12, "font-weight": 800, "text-anchor": "middle"}); label.textContent = destination.name; group.append(label);
    const choose = () => { selectedDestinationId = destination.destination_id; setContext("command"); render(data); };
    group.addEventListener("click", choose); group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") choose(); });
    destinationLayer.append(group);
  });

  (data.packages || []).forEach((item) => {
    const [x, y] = point(item.position);
    const selected = selectedPackageIds.has(item.package_id);
    const job = data.jobs.find((candidate) => candidate.job_id === item.active_job_id);
    const stateText = packageState(item, job);
    const color = {HIGH_VALUE: "#d9b85c", MEDICAL: "#f06f6f", FRAGILE: "#c58ae2", STANDARD: "#6ca6e8"}[item.category] || "#6ca6e8";
    const dimensions = item.category === "FRAGILE" ? {x: -14, y: -20, width: 28, height: 36} : item.category === "HIGH_VALUE" ? {x: -19, y: -16, width: 38, height: 28} : item.category === "MEDICAL" ? {x: -17, y: -17, width: 34, height: 30} : {x: -16, y: -18, width: 32, height: 32};
    const selectable = item.status === "AVAILABLE";
    const group = svgElement("g", {class: `package-node category-${item.category.toLowerCase()} state-${item.status.toLowerCase()} ${selected ? "selected" : ""} ${selectable ? "selectable" : "locked"}`, transform: `translate(${x} ${y})`, tabindex: selectable ? 0 : -1, role: "button", "aria-pressed": String(selected), "aria-disabled": String(!selectable), "aria-label": `${item.name}, ${item.category}, priority ${item.priority}, ${item.status}`, "data-tooltip-id": item.package_id});
    const title = svgElement("title"); title.textContent = `${item.name}\n${item.category.replace("_", " ")} · Priority ${item.priority}\n${stateText}`; group.append(title);
    if (!["AVAILABLE", "DELIVERED"].includes(item.status)) group.setAttribute("opacity", ".82");
    group.append(svgElement("rect", {...dimensions, fill: "#ffffff", stroke: color, "stroke-width": selected ? 4 : 2, filter: selected ? "url(#soft-glow)" : ""}));
    if (item.category === "STANDARD") {
      group.append(svgElement("path", {d: "M-16 -18 L-8 -26 H24 L16 -18 Z", fill: color, opacity: ".75"}));
      group.append(svgElement("path", {d: "M16 -18 L24 -26 V6 L16 14 Z", fill: color, opacity: ".42"}));
    } else if (item.category === "HIGH_VALUE") {
      group.append(svgElement("rect", {x: -14, y: -11, width: 28, height: 18, fill: "none", stroke: color, "stroke-width": 1, opacity: ".7"}));
    } else if (item.category === "FRAGILE") {
      group.append(svgElement("path", {d: "M-14 -20 L0 -29 L14 -20", fill: color, opacity: ".62"}));
    }
    const icon = svgElement("text", {x: 0, y: 4, fill: color, "font-size": 12, "font-weight": 950, "text-anchor": "middle"}); icon.textContent = item.category === "MEDICAL" ? "+" : item.category === "HIGH_VALUE" ? "◆" : item.category === "FRAGILE" ? "!" : "■"; group.append(icon);
    const label = svgElement("text", {x: 0, y: 31, fill: selected ? "#0b4d7a" : "#284a5a", "font-size": 13, "font-weight": 800, "text-anchor": "middle"}); label.textContent = packageLabel(item); group.append(label);
    const stateColor = item.status === "AVAILABLE" ? "#65c99a" : item.status === "DELIVERED" ? "#65c99a" : item.status === "RECOVERY_PENDING" ? "#f06f6f" : "#f3b95f";
    const status = svgElement("text", {x: 0, y: 44, fill: stateColor, "font-size": 10, "font-weight": 800, "text-anchor": "middle"}); status.textContent = stateText; group.append(status);
    if (item.status === "DELIVERED") group.append(svgElement("circle", {cx: 17, cy: -17, r: 7, fill: "#dff7ed", stroke: "#159a7a", "stroke-width": 1.5}));
    if (item.status === "DELIVERED") { const check = svgElement("text", {x: 17, y: -14, fill: "#0b7d61", "font-size": 8, "font-weight": 950, "text-anchor": "middle"}); check.textContent = "✓"; group.append(check); }
    const choose = () => {
      if (!selectable) { showToast(`${item.package_id} is ${stateText}`); return; }
      if (selected) selectedPackageIds.delete(item.package_id);
      else selectedPackageIds.add(item.package_id);
      selectedDecisionJobId = null;
      lastBatchResult = null;
      setContext("command");
      render(data);
    };
    group.addEventListener("click", choose); group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") choose(); });
    packageLayer.append(group);
  });
  const hovered = (data.packages || []).find((item) => item.package_id === hoveredPackageId);
  if (hovered) updateMapTooltip(hovered, packageState(hovered, data.jobs.find((job) => job.job_id === hovered.active_job_id)));

  data.jobs.forEach((job) => {
    const complete = job.status === "COMPLETED";
    if (data.simulation.mode === "scripted") [[job.pickup, "P", "PICKUP"], [job.dropoff, "D", "DROPOFF"]].forEach(([location, letter, label]) => {
      const [x, y] = point(location);
      const group = svgElement("g", {opacity: complete ? ".28" : ".85"});
      group.append(svgElement("rect", {x: x - 12, y: y - 12, width: 24, height: 24, rx: 4, fill: "#ffffff", stroke: letter === "P" ? "#1677d2" : "#159a7a", "stroke-width": 2}));
      const text = svgElement("text", {x, y: y + 4, fill: "#173746", "font-size": 11, "font-weight": 800, "text-anchor": "middle"}); text.textContent = letter; group.append(text);
      const caption = svgElement("text", {x, y: y + 25, fill: "#758896", "font-size": 8, "text-anchor": "middle"}); caption.textContent = `${job.job_id} ${label}`; group.append(caption);
      jobLayer.append(group);
    });
    if (job.recovery_point) {
      const [x, y] = point(job.recovery_point);
      const ring = svgElement("circle", {cx: x, cy: y, r: 18, fill: "none", stroke: "#f3b95f", "stroke-width": 3, "stroke-dasharray": "4 4"});
      jobLayer.append(ring);
      const label = svgElement("text", {x, y: y - 25, fill: "#f3b95f", "font-size": 9, "font-weight": 800, "text-anchor": "middle"}); label.textContent = "RECOVERY"; jobLayer.append(label);
    }
  });

  data.trajectories.forEach((route) => {
    const robot = data.robots.find((item) => item.id === route.robot_id);
    if (!robot) return;
    const path = (route.estimated_path || [route.current_position, route.target_position]).map(point);
    if (path.length < 2) return;
    routeLayer.append(svgElement("polyline", {points: path.map(([x, y]) => `${x},${y}`).join(" "), fill: "none", stroke: robotColor(robot), "stroke-width": route.yielding ? 5 : 4, "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-dasharray": route.yielding ? "4 9" : "none", opacity: ".92", "marker-end": "url(#route-arrow)"}));
    const labelPoint = path[Math.min(1, path.length - 1)];
    const routeLabel = svgElement("text", {x: labelPoint[0], y: labelPoint[1] - 12, fill: robotColor(robot), "font-size": 13, "font-weight": 800, "text-anchor": "middle"}); routeLabel.textContent = `Robot ${robot.id}${route.yielding ? " · Yielding" : ""}`; routeLayer.append(routeLabel);
  });

  data.conflicts.forEach((conflict) => {
    const [x, y] = point(conflict.conflict_point);
    conflictLayer.append(svgElement("circle", {cx: x, cy: y, r: 28, fill: "#f3b95f22", stroke: "#f3b95f", "stroke-width": 2, "stroke-dasharray": "5 4", filter: "url(#soft-glow)"}));
    const icon = svgElement("text", {x, y: y + 6, fill: "#ffd38e", "font-size": 22, "font-weight": 900, "text-anchor": "middle"}); icon.textContent = "!"; conflictLayer.append(icon);
    const text = svgElement("text", {x, y: y + 46, fill: "#c9780a", "font-size": 13, "font-weight": 800, "text-anchor": "middle"}); text.textContent = `${conflict.robot_a} ↔ ${conflict.robot_b} · ${conflict.right_of_way} proceeds`; conflictLayer.append(text);
  });

  data.robots.forEach((robot) => {
    const [x, y] = point(robot.position);
    const color = robotColor(robot);
    const group = svgElement("g", {class: `robot-node ${selectedRobotId === robot.id ? "selected" : ""}`, transform: `translate(${x} ${y})`, role: "button", tabindex: 0, "aria-label": `Robot ${robot.id}, ${robot.state}`});
    group.append(svgElement("rect", {x: -27, y: -27, width: 54, height: 54, rx: 12, fill: "#ffffff", stroke: color, "stroke-width": 5, filter: "url(#soft-glow)"}));
    group.append(svgElement("rect", {x: -17, y: -17, width: 34, height: 34, rx: 7, fill: color, opacity: ".16"}));
    const id = svgElement("text", {x: 0, y: 6, fill: "#163647", "font-size": 18, "font-weight": 800, "text-anchor": "middle"}); id.textContent = robot.id; group.append(id);
    const badge = svgElement("rect", {x: -38, y: 34, width: 76, height: 20, rx: 10, fill: "#ffffff", stroke: color, "stroke-width": 1.5}); group.append(badge);
    const state = svgElement("text", {x: 0, y: 48, fill: color, "font-size": 10, "font-weight": 800, "text-anchor": "middle"}); state.textContent = robot.state; group.append(state);
    const choose = () => { selectedRobotId = robot.id; setContext("robot"); renderRobots(data); };
    group.addEventListener("click", choose); group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") choose(); });
    robotLayer.append(group);
  });

  const alert = $("map-alert");
  if (data.conflicts.length) {
    clearTimeout(trafficIdleResetTimer);
    trafficIdleResetTimer = null;
    const now = Date.now();
    const conflictKey = data.conflicts.map((conflict) => `${conflict.robot_a}:${conflict.robot_b}:${conflict.right_of_way}:${conflict.yielding_robot}`).join("|");
    if (lastTrafficAlertKey === null) {
      trafficAlertExpandedUntil = now + 4800;
      clearTimeout(trafficAlertTimer);
      trafficAlertTimer = setTimeout(() => alert.classList.add("minimized"), 4800);
    }
    lastTrafficAlertKey = conflictKey;
    alert.classList.toggle("minimized", now >= trafficAlertExpandedUntil);
    alert.innerHTML = data.conflicts.slice(0, 2).map((conflict, index) => {
      const details = [conflict.robot_a, conflict.robot_b].map((robotId) => {
        const robot = data.robots.find((item) => item.id === robotId);
        const job = data.jobs.find((item) => item.job_id === robot?.job_id);
        const cargo = data.packages.find((item) => item.package_id === job?.package_id);
        return {robotId, cargo: cargo?.name || job?.package_id || "Empty", priority: job?.priority ?? "—"};
      });
      return `<article class="traffic-alert-card ${index ? "secondary" : ""}"><b>${icon("conflict")} NEXUS TRAFFIC DECISION</b><span><em>${esc(details[0].robotId)}</em> ${esc(details[0].cargo)} · P${esc(details[0].priority)}</span><span><em>${esc(details[1].robotId)}</em> ${esc(details[1].cargo)} · P${esc(details[1].priority)}</span><strong>${esc(conflict.right_of_way)} HAS RIGHT OF WAY</strong><small>${esc(conflict.reason)} · ${esc(conflict.yielding_robot)} yielding · ${conflict.predicted_distance.toFixed(2)}m</small></article>`;
    }).join("");
    alert.classList.remove("hidden");
  } else {
    alert.classList.add("hidden");
    if (!trafficIdleResetTimer) {
      trafficIdleResetTimer = setTimeout(() => {
        lastTrafficAlertKey = null;
        trafficAlertExpandedUntil = 0;
        alert.classList.remove("minimized");
        trafficIdleResetTimer = null;
      }, 2000);
    }
  }
}

function renderRobots(data) {
  $("robot-summary").innerHTML = data.robots.map((robot) => `
    <button class="robot-summary-card ${selectedRobotId === robot.id ? "selected" : ""}" data-robot-id="${esc(robot.id)}">
      <strong>[${esc(robot.id)}]</strong>
      <span class="summary-state ${stateClass(robot.state)}">${esc(robot.state)}</span>
      <span class="summary-bars">${robot.battery.toFixed(0)}% BAT · ${robot.health_percent.toFixed(0)}% HEALTH · ${robot.reliability_score.toFixed(0)}% REL</span>
      <span class="summary-job">${esc(robot.job_id || "NO JOB")}</span>
    </button>`).join("");
  document.querySelectorAll(".robot-summary-card").forEach((card) => card.addEventListener("click", () => {
    selectedRobotId = card.dataset.robotId;
    setContext("robot");
    renderRobots(data);
  }));
  const robot = data.robots.find((item) => item.id === selectedRobotId) || data.robots[0];
  if (!robot) return;
  $("robot-detail").innerHTML = `
    <div class="robot-detail-hero"><strong>ROBOT ${esc(robot.id)}</strong><span class="state-pill ${stateClass(robot.state)}">${esc(robot.state)}</span></div>
    <dl>
      <dt>POSITION</dt><dd>${robot.x.toFixed(2)}, ${robot.y.toFixed(2)}</dd>
      <dt>BATTERY</dt><dd>${robot.battery.toFixed(1)}%</dd>
      <dt>HEALTH</dt><dd>${robot.health_percent.toFixed(0)}%</dd>
      <dt>RELIABILITY</dt><dd>${robot.reliability_score.toFixed(0)}%</dd>
      <dt>ACTIVE JOB</dt><dd>${esc(robot.job_id || "—")}</dd>
      <dt>TASK</dt><dd>${esc(robot.task_state)}</dd>
      <dt>PRIORITY</dt><dd>${robot.job_priority ?? "—"}</dd>
    </dl>
    ${robot.yielding ? `<div class="robot-detail-note">HOLDING · ${esc(robot.yield_reason)}</div>` : ""}
    ${!robot.available ? `<div class="robot-detail-note">${esc(robot.unavailable_reason || "Operationally unavailable")}</div>` : ""}`;
}

function renderJobs(data) {
  $("job-count").textContent = `${data.jobs.length} JOBS`;
  const activeStates = new Set(["ASSIGNED", "TO_PICKUP", "PICKED_UP", "TO_DROPOFF"]);
  const groups = [
    ["ACTIVE", data.jobs.filter((job) => activeStates.has(job.status))],
    ["WAITING", data.jobs.filter((job) => job.status === "PENDING")],
    ["COMPLETED", data.jobs.filter((job) => job.status === "COMPLETED")],
  ];
  $("job-list").innerHTML = groups.filter(([, jobs]) => jobs.length).map(([label, jobs]) => `<div class="task-group"><h3>${label}<span>${jobs.length}</span></h3>${jobs.map((job) => `
    <article class="job-card ${job.priority === 10 ? "critical" : ""} ${job.status === "COMPLETED" ? "complete" : ""}" data-job-id="${esc(job.job_id)}">
      <strong>${esc(job.job_id)}</strong><span class="priority-tag">P${job.priority}</span>
      <span class="package">${esc(job.package_id)}</span>
      <div class="job-meta"><span><b>${esc(job.status)}</b></span><span>ROBOT <b>${esc(job.assigned_robot || "UNASSIGNED")}</b></span>${job.queue_position ? `<span>QUEUE <b>#${job.queue_position}</b></span>` : ""}${job.reassignment_count ? `<span>HANDOFF <b>${job.reassignment_count}</b></span>` : ""}</div>
    </article>`).join("")}</div>`).join("") || `<div class="custody-empty">No fleet tasks submitted</div>`;
  document.querySelectorAll(".job-card[data-job-id]").forEach((card) => card.addEventListener("click", () => { selectedDecisionJobId = card.dataset.jobId; setContext("decision"); renderDecision(data); }));
}

function renderEvents(data) {
  const events = [...data.events].reverse().slice(0, 30);
  const newest = events[0];
  $("latest-event").textContent = newest ? `Latest: ${newest.message}` : "Latest: Waiting for fleet telemetry";
  $("event-count").textContent = `${data.events.length} EVENTS`;
  $("event-list").innerHTML = events.map((event) => `
    <div class="event-row ${esc(event.severity)}">
      <span class="event-time">${event.time.toFixed(1)}s</span>
      <span class="event-category">${esc(event.category)}</span>
      <span class="event-message">${esc(event.message)}</span>
    </div>`).join("");
  const important = events.find((event) => ["warning", "critical", "success"].includes(event.severity));
  const eventKey = important ? `${important.time}:${important.category}:${important.message}` : null;
  if (document.body.classList.contains("focus-mode") && focusEventsReady && eventKey && eventKey !== lastFocusEventKey) {
    showToast(`${important.category} · ${important.message}`);
  }
  lastFocusEventKey = eventKey;
  focusEventsReady = true;
}

function renderInventory(data) {
  const items = data.packages || [];
  $("inventory-count").textContent = `${items.length} ITEMS`;
  $("inventory-list").innerHTML = items.length ? items.map((item) => `
    <button type="button" class="inventory-row" data-inventory-package="${esc(item.package_id)}">
      <span class="inventory-symbol category-${esc(item.category.toLowerCase())}">${esc(packageLabel(item))}</span>
      <span><b>${esc(item.name)}</b><small>${esc(item.location)} · Priority ${item.priority}</small></span>
      <em>${esc(item.status.replaceAll("_", " "))}</em>
    </button>`).join("") : `<div class="custody-empty">Inventory is available in Fleet Command mode.</div>`;
  document.querySelectorAll("[data-inventory-package]").forEach((row) => row.addEventListener("click", () => {
    const item = items.find((candidate) => candidate.package_id === row.dataset.inventoryPackage);
    if (!item) return;
    if (item.status === "AVAILABLE") selectedPackageIds.add(item.package_id);
    setContext("command");
    render(data);
  }));
}

const custodyLabels = {
  JOB_CREATED: "JOB CREATED",
  CUSTODY_ASSIGNED: "CUSTODY → C",
  PACKAGE_PICKED_UP: "PICKED UP",
  ROBOT_UNAVAILABLE: "C FAILED",
  RECOVERY_POINT_CREATED: "RECOVERY POINT",
  CUSTODY_TRANSFER: "TRANSFER C → A",
  PACKAGE_DELIVERED: "DELIVERED",
};

function renderCustody(data) {
  const chain = data.solana || {enabled: false, attestations: []};
  const badge = $("custody-state");
  badge.textContent = chain.enabled ? "DEVNET" : "OFFLINE";
  badge.classList.toggle("offline", !chain.enabled);
  const wallet = chain.wallet ? `${chain.wallet.slice(0, 4)}…${chain.wallet.slice(-4)}` : "NO WALLET";
  $("custody-summary").textContent = chain.enabled
    ? `${wallet} · ${chain.confirmed} VERIFIED · ${chain.pending} PENDING`
    : "OFFLINE / DEMO CONTINUES";
  const records = chain.attestations || [];
  $("custody-list").innerHTML = records.length ? records.map((item) => {
    const status = String(item.chain_status || "PENDING").toLowerCase();
    const mark = status === "confirmed" ? "✓" : status === "failed" ? "×" : status === "not_required" ? "—" : "⋯";
    const signature = item.transaction_signature;
    const safeUrl = String(item.explorer_url || "").startsWith("https://explorer.solana.com/tx/") ? item.explorer_url : null;
    const tx = signature && safeUrl
      ? `<a href="${esc(safeUrl)}" target="_blank" rel="noopener" title="Open on Solana Explorer">${esc(signature.slice(0, 5))}…${esc(signature.slice(-5))}</a>`
      : `<span>${esc(status.replace("_", " "))}</span>`;
    return `<div class="custody-row ${status}"><i>${mark}</i><b>${esc(custodyLabels[item.event] || item.event)}</b>${tx}</div>`;
  }).join("") : `<div class="custody-empty">MEDICAL-CRITICAL attestations appear here</div>`;
}

function renderCommand(data) {
  const destinations = data.destinations || [];
  const select = $("destination-select");
  const options = `<option value="">Select zone…</option>${destinations.map((item) => `<option value="${esc(item.destination_id)}">${esc(item.name.toUpperCase())}</option>`).join("")}`;
  if (select.dataset.mode !== data.simulation.mode) { select.innerHTML = options; select.dataset.mode = data.simulation.mode; }
  select.value = selectedDestinationId || "";
  const packages = data.packages || [];
  for (const packageId of [...selectedPackageIds]) {
    const item = packages.find((candidate) => candidate.package_id === packageId);
    if (!item || item.status !== "AVAILABLE") selectedPackageIds.delete(packageId);
  }
  const items = [...selectedPackageIds].map((packageId) => packages.find((item) => item.package_id === packageId)).filter(Boolean);
  const count = items.length;
  if (count) {
    const destination = destinations.find((item) => item.destination_id === selectedDestinationId);
    $("object-summary").innerHTML = `<b>${count} ITEM${count === 1 ? "" : "S"} READY <em>${destination ? `→ ${esc(destination.name.toUpperCase())}` : "SELECT DESTINATION"}</em></b><span>${esc([...new Set(items.map((item) => item.category.replace("_", " ")))].join(" · "))}</span><span>INDIVIDUAL PROFILES PRESERVED</span>`;
  } else {
    $("object-summary").innerHTML = `<b>SELECT PACKAGES ON THE MAP</b><span>NEXUS divides the work across the fleet.</span>`;
  }
  $("selection-count").textContent = count;
  $("selection-float-count").textContent = `${count} ITEM${count === 1 ? "" : "S"} SELECTED`;
  $("selection-float").classList.toggle("visible", count > 0);
  $("selected-items").innerHTML = count ? items.map((item) => `<div class="selected-item"><span><b>${esc(item.package_id)}</b><small>${esc(item.category.replace("_", " "))} · P${item.priority}</small></span><button type="button" data-remove-package="${esc(item.package_id)}" aria-label="Remove ${esc(item.package_id)}">×</button></div>`).join("") : `<p>Choose one or more available packages.</p>`;
  $("clear-selection-btn").disabled = count === 0;
  if (currentContext === "command") $("context-title").textContent = count ? `${count} CARGO SELECTED` : "FLEET COMMAND";
  $("priority-output").textContent = $("priority-input").value;
  setButtonLabel("dispatch-btn", "dispatch", `DISPATCH ${count} JOB${count === 1 ? "" : "S"}`);
  $("dispatch-btn").disabled = !count || !selectedDestinationId || data.simulation.mode !== "manual";
}

function renderDecision(data) {
  const history = data.decision_history || [];
  if (!selectedDecisionJobId && lastBatchResult) {
    const batch = lastBatchResult;
    $("decision-content").className = "decision-content batch-result";
    $("decision-content").innerHTML = `<div class="batch-result-hero"><span>FLEET DISPATCH</span><b>✓ ${batch.submitted} JOBS SUBMITTED</b><p><strong>${batch.active} ACTIVE</strong><strong>${batch.waiting} WAITING</strong></p></div><div class="batch-result-list">${batch.results.map((item) => `<button type="button" data-decision-job="${esc(item.job_id)}"><span><b>${esc(item.package_id)}</b><small>${esc(item.category.replace("_", " "))} · P${item.priority}</small></span><em>${item.assigned_robot_id ? `ROBOT ${esc(item.assigned_robot_id)} SELECTED` : "QUEUED"}</em><i>VIEW AUCTION ›</i></button>`).join("")}</div>`;
    return;
  }
  const decision = selectedDecisionJobId
    ? [...history].reverse().find((item) => item.job_id === selectedDecisionJobId) || data.decision
    : data.decision;
  if (!decision) {
    $("decision-content").className = "decision-content empty";
    $("decision-content").textContent = "Dispatch selected cargo to compare every eligible robot.";
    return;
  }
  $("decision-content").className = "decision-content";
  const bids = decision.bids.map((bid) => {
    const c = bid.components;
    return `<div class="bid-card ${bid.robot_id === decision.winner ? "winner" : ""} ${!bid.eligible ? "ineligible" : ""}">
      <header><b>ROBOT ${esc(bid.robot_id)}</b><strong>${bid.total == null ? "—" : bid.total.toFixed(2)}</strong></header>
      <span>DIST ${c.distance.value.toFixed(2)}m <i>+${c.distance.cost.toFixed(2)}</i></span>
      <span>BAT ${c.battery.value.toFixed(0)}% <i>+${c.battery.cost.toFixed(2)}</i></span>
      <span>HEALTH ${c.health.value.toFixed(0)}% <i>+${c.health.cost.toFixed(2)}</i></span>
      <span>RELIAB ${c.reliability.value.toFixed(0)}% <i>+${c.reliability.cost.toFixed(2)}</i></span>
      <span>TRAFFIC ${esc(c.traffic.value)} <i>+${c.traffic.cost.toFixed(2)}</i></span>
      <span>LOAD ${esc(c.workload.value)} <i>+${c.workload.cost.toFixed(2)}</i></span>
      ${bid.reason ? `<small>${esc(bid.reason)}</small>` : ""}
    </div>`;
  }).join("");
  const queued = decision.type === "QUEUED";
  const reasons = (decision.reasons || []).map((reason) => `<span>${icon("complete")}${esc(reason)}</span>`).join("");
  $("decision-content").innerHTML = `<div class="decision-winner ${queued ? "queued" : ""}"><span>${queued ? "JOB QUEUED" : decision.type === "RECOVERY_DECISION" ? "RECOVERY DECISION" : "ASSIGNMENT DECISION"}</span><b>${queued ? `${icon("queue")} ${esc(decision.job_id)} WAITING` : `${icon("complete")} ROBOT ${esc(decision.winner)} SELECTED`}</b><p>${esc(decision.comparison)}</p></div>${reasons ? `<div class="decision-reasons">${reasons}</div>` : ""}<div class="bid-grid">${bids}</div>`;
  if (decision.winner) $("failure-robot").value = decision.winner;
}

function render(data) {
  latest = data;
  const sim = data.simulation, stats = data.stats;
  $("phase-label").textContent = sim.phase;
  $("tick-value").textContent = sim.tick.toLocaleString();
  $("time-value").textContent = `${sim.time.toFixed(2)}s`;
  $("speed-value").textContent = `${sim.demo_speed.toFixed(2)}×`;
  $("stat-online").textContent = `${stats.robots_online} / ${stats.robots_total}`;
  $("stat-active").textContent = stats.active_jobs;
  $("stat-pending").textContent = stats.pending_jobs;
  $("stat-complete").textContent = stats.jobs_completed;
  $("stat-busy").textContent = stats.robots_busy;
  $("stat-available").textContent = stats.robots_available;
  $("stat-conflicts").textContent = stats.traffic_conflicts_prevented;
  $("stat-reassignments").textContent = stats.automatic_reassignments;
  setButtonLabel("start-btn", "analytics", sim.mode === "scripted" && sim.running ? "Scripted Running" : "Scripted Demo");
  $("start-btn").disabled = sim.mode === "scripted" && sim.running;
  $("fleet-mode-btn").classList.toggle("active", sim.mode === "manual");
  $("start-btn").classList.toggle("active", sim.mode === "scripted");
  $("fleet-status-chip").innerHTML = `<i></i> ${stats.robots_online} ROBOT${stats.robots_online === 1 ? "" : "S"} ONLINE`;
  $("traffic-test-btn").classList.toggle("hidden", sim.mode !== "manual");
  document.querySelectorAll(".scripted-only").forEach((item) => item.classList.toggle("hidden", sim.mode !== "scripted"));
  $("pause-btn").disabled = !sim.running;
  $("priority-btn").disabled = data.jobs.some((job) => job.job_id === "JOB-URGENT");
  renderMap(data); renderRobots(data); renderJobs(data); renderInventory(data); renderCommand(data); renderDecision(data); renderCustody(data); renderEvents(data);
}

function showToast(message) {
  const toast = $("toast"); toast.textContent = message; toast.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 2400);
}

async function command(path, successMessage = "Command accepted") {
  try {
    const response = await fetch(path, {method: "POST"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Command failed");
    render(payload); showToast(successMessage);
  } catch (error) { showToast(error.message); }
}

async function dispatchFleet() {
  if (!selectedPackageIds.size || !selectedDestinationId) return;
  const packageIds = [...selectedPackageIds];
  const destinationId = selectedDestinationId;
  try {
    const priority = $("priority-override").checked ? Number($("priority-input").value) : null;
    const response = await fetch("/api/jobs/batch", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        items: packageIds.map((packageId) => ({package_id: packageId, destination: destinationId})),
        priority,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Dispatch failed");
    lastBatchResult = payload.batch;
    selectedPackageIds.clear();
    if (selectedDestinationId === destinationId) selectedDestinationId = null;
    selectedDecisionJobId = null;
    $("priority-override").checked = false; $("priority-input").disabled = true; $("priority-input").value = 5;
    const feedback = `${payload.batch.submitted} JOBS SUBMITTED · ${payload.batch.active} ACTIVE · ${payload.batch.waiting} WAITING`;
    setContext("decision"); render(payload);
    showToast(feedback);
  } catch (error) { showToast(error.message); }
}

function selectTrafficTest() {
  if (!latest || latest.simulation.mode !== "manual") return;
  selectedPackageIds.clear();
  ["BOX-101", "MED-KIT-01", "FR-301"].forEach((packageId) => {
    const item = latest.packages.find((candidate) => candidate.package_id === packageId);
    if (item?.status === "AVAILABLE") selectedPackageIds.add(packageId);
  });
  selectedDestinationId = "OUTBOUND";
  selectedDecisionJobId = null;
  lastBatchResult = null;
  setContext("command");
  render(latest);
  showToast(`${selectedPackageIds.size} TRAFFIC-PRONE ITEMS SELECTED · REVIEW AND DISPATCH`);
}

function setConnection(connected) {
  const chip = $("connection-chip"); chip.textContent = connected ? "LIVE LINK" : "RECONNECTING"; chip.classList.toggle("connected", connected);
}

async function poll() {
  try { const response = await fetch("/api/snapshot"); render(await response.json()); } catch (_) {}
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onopen = () => { setConnection(true); clearInterval(pollTimer); pollTimer = null; };
  socket.onmessage = (event) => render(JSON.parse(event.data));
  socket.onclose = () => { setConnection(false); if (!pollTimer) pollTimer = setInterval(poll, 1000); setTimeout(connect, 1500); };
  socket.onerror = () => socket.close();
}

$("start-btn").addEventListener("click", () => command("/api/demo/start"));
$("fleet-mode-btn").addEventListener("click", () => { selectedPackageIds.clear(); selectedDestinationId = null; selectedDecisionJobId = null; lastBatchResult = null; command("/api/demo/fleet"); });
$("traffic-test-btn").addEventListener("click", () => { selectTrafficTest(); setDemoTools(false); });
$("pause-btn").addEventListener("click", () => { command("/api/demo/pause"); setDemoTools(false); });
$("reset-btn").addEventListener("click", () => { selectedPackageIds.clear(); selectedDestinationId = null; selectedDecisionJobId = null; lastBatchResult = null; command("/api/demo/reset"); setDemoTools(false); });
$("priority-btn").addEventListener("click", () => { command("/api/demo/priority"); setDemoTools(false); });
$("failure-btn").addEventListener("click", () => { command(`/api/demo/failure/${$("failure-robot").value}`, `ROBOT ${$("failure-robot").value} UNAVAILABLE · RECOVERY INITIATED`); setDemoTools(false); });
$("destination-select").addEventListener("change", (event) => { selectedDestinationId = event.target.value || null; if (latest) render(latest); });
$("priority-input").addEventListener("input", (event) => { $("priority-output").textContent = event.target.value; });
$("priority-override").addEventListener("change", (event) => { $("priority-input").disabled = !event.target.checked; });
$("dispatch-btn").addEventListener("click", dispatchFleet);
$("clear-selection-btn").addEventListener("click", () => { selectedPackageIds.clear(); if (latest) render(latest); });
$("clear-map-selection").addEventListener("click", () => { selectedPackageIds.clear(); if (latest) render(latest); });
$("selected-items").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-package]");
  if (!button) return;
  selectedPackageIds.delete(button.dataset.removePackage);
  if (latest) render(latest);
});
$("decision-content").addEventListener("click", (event) => {
  const button = event.target.closest("[data-decision-job]");
  if (!button) return;
  selectedDecisionJobId = button.dataset.decisionJob;
  if (latest) render(latest);
});
$("theme-toggle").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
document.querySelectorAll(".context-tab").forEach((tab) => tab.addEventListener("click", () => setContext(tab.dataset.context)));
document.querySelectorAll(".nav-drawer-button[data-nav-context]").forEach((tab) => tab.addEventListener("click", () => setContext(tab.dataset.navContext)));
$("nav-menu-btn").addEventListener("click", () => setNavDrawer(!$("nav-drawer").classList.contains("open")));
$("nav-close-btn").addEventListener("click", () => setNavDrawer(false));
document.querySelector("[data-close-nav]").addEventListener("click", () => setNavDrawer(false));
$("demo-tools-btn").addEventListener("click", () => setDemoTools(!$("demo-tools-menu").classList.contains("open")));
$("demo-tools-close").addEventListener("click", () => setDemoTools(false));
$("nav-events-btn").addEventListener("click", () => {
  const drawer = $("event-drawer");
  const expanded = drawer.classList.toggle("expanded");
  $("event-drawer-toggle").setAttribute("aria-expanded", String(expanded));
  $("event-drawer-arrow").textContent = expanded ? "▾" : "▴";
  setNavDrawer(false);
});
$("sidebar-close-btn").addEventListener("click", () => setSidebarCollapsed(true));
$("context-toggle-btn").addEventListener("click", () => setSidebarCollapsed(!sidebarCollapsed));
$("event-drawer-toggle").addEventListener("click", () => {
  const expanded = $("event-drawer").classList.toggle("expanded");
  $("event-drawer-toggle").setAttribute("aria-expanded", String(expanded));
  $("event-drawer-arrow").textContent = expanded ? "▾" : "▴";
});
$("focus-btn").addEventListener("click", () => toggleFocus());
$("focus-exit-btn").addEventListener("click", () => toggleFocus(false));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  setNavDrawer(false);
  setDemoTools(false);
});
$("warehouse-map").addEventListener("pointermove", (event) => {
  const target = event.target.closest?.(".package-node");
  const tooltip = $("map-tooltip");
  if (!target) { hoveredPackageId = null; tooltip.classList.add("hidden"); return; }
  hoveredPackageId = target.dataset.tooltipId;
  if (latest) {
    const item = latest.packages.find((candidate) => candidate.package_id === hoveredPackageId);
    const job = latest.jobs.find((candidate) => candidate.job_id === item?.active_job_id);
    updateMapTooltip(item, item ? packageState(item, job) : "");
  }
  const wrap = document.querySelector(".warehouse-wrap").getBoundingClientRect();
  const left = clamp(event.clientX - wrap.left + 14, 8, wrap.width - 205);
  const top = clamp(event.clientY - wrap.top - 72, 8, wrap.height - 78);
  tooltip.style.left = `${left}px`; tooltip.style.top = `${top}px`; tooltip.classList.remove("hidden");
});
$("warehouse-map").addEventListener("pointerleave", () => { hoveredPackageId = null; $("map-tooltip").classList.add("hidden"); });
$("map-alert").addEventListener("click", () => {
  const minimized = $("map-alert").classList.toggle("minimized");
  clearTimeout(trafficAlertTimer);
  if (!minimized) {
    trafficAlertExpandedUntil = Date.now() + 4800;
    trafficAlertTimer = setTimeout(() => $("map-alert").classList.add("minimized"), 4800);
  }
});
applyTheme(document.documentElement.dataset.theme);
setSidebarCollapsed(true);
poll(); connect();
