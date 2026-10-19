"use strict";

const byId = id => document.getElementById(id);
const activeStatuses = new Set(["queued", "running", "reconciling", "waiting_approval"]);
let csrf = "";
let eventStream = null;
let refreshTimer = null;
let refreshPromise = null;
let eventRefresh = null;
let state = null;
let projectId = null;
let workflowId = null;
let runId = null;
let logTimer = null;
let durationTimer = null;
let selectedAttempt = null;
let organizations = [];
let activeOrganization = null;
let runnerAccessId = null;

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  const current = theme || "system";
  byId("theme-toggle").title = `Color theme: ${current}. Click to switch.`;
  byId("theme-toggle").setAttribute("aria-label", `Color theme: ${current}. Click to switch.`);
}

applyTheme(localStorage.getItem("aorch-theme"));
byId("theme-toggle").addEventListener("click", () => {
  const current = localStorage.getItem("aorch-theme");
  const next = current === "light" ? "dark" : current === "dark" ? null : "light";
  if (next) localStorage.setItem("aorch-theme", next);
  else localStorage.removeItem("aorch-theme");
  applyTheme(next);
});

byId("notification-kind").addEventListener("change", renderNotificationSettings);
byId("notification-event-settings").addEventListener("change", async () => {
  try {
    const events = [...byId("notification-event-settings").querySelectorAll("input:checked")].map(input => input.value);
    await api("/admin/notification-events", { events }, "PUT");
    await refresh();
    notify("Notification preferences saved.");
  } catch (error) { showError(error); }
});
byId("notification-settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const kind = byId("notification-kind").value;
    await api(`/admin/notification-targets/${encodeURIComponent(kind)}`, { url: byId("notification-url").value }, "PUT");
    byId("notification-url").value = "";
    await refresh();
    notify("Notification endpoint saved for this organization.");
  } catch (error) { showError(error); }
});
byId("notification-remove").addEventListener("click", async () => {
  try {
    const kind = byId("notification-kind").value;
    await api(`/admin/notification-targets/${encodeURIComponent(kind)}`, undefined, "DELETE");
    await refresh();
    notify("Notification endpoint removed.");
  } catch (error) { showError(error); }
});

async function api(path, body, method) {
  const response = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch (_) { /* Keep the HTTP status text. */ }
    throw new Error(data.detail || response.statusText || `HTTP ${response.status}`);
  }
  return response.status === 204 ? {} : response.json();
}

function notify(message, error = false) {
  const status = byId("message");
  status.textContent = message;
  status.classList.toggle("status-failed", error);
}

function formatTime(value) {
  return value ? new Date(value * 1000).toLocaleString() : "";
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${remaining}s`;
  return `${remaining}s`;
}

function updateDurationLabels() {
  const now = Date.now() / 1000;
  document.querySelectorAll("[data-duration-start]").forEach(element => {
    const start = Number(element.dataset.durationStart);
    const end = element.dataset.durationEnd ? Number(element.dataset.durationEnd) : now;
    element.textContent = formatDuration(Math.max(0, end - start));
  });
  document.querySelectorAll("[data-duration-base][data-duration-since]").forEach(element => {
    element.textContent = formatDuration(Number(element.dataset.durationBase) + Math.max(0, now - Number(element.dataset.durationSince)));
  });
}

function makeButton(text, callback, className = "button button-secondary") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.addEventListener("click", () => callback().catch(showError));
  return button;
}

function collectWorkflowInputs(workflow) {
  const dialog = byId("workflow-input-dialog");
  const form = byId("workflow-input-form");
  const fields = byId("workflow-input-fields");
  const schema = workflow?.input_schema || { type: "object", properties: {} };
  fields.replaceChildren();
  const controls = new Map();
  for (const [name, definition] of Object.entries(schema.properties || {})) {
    const label = document.createElement("label");
    label.textContent = `${name}${(schema.required || []).includes(name) ? " *" : ""}`;
    const control = document.createElement(definition.type === "object" || definition.type === "array" ? "textarea" : "input");
    control.name = name;
    control.required = (schema.required || []).includes(name);
    if (definition.enum) {
      const select = document.createElement("select");
      select.name = name;
      select.required = control.required;
      for (const value of definition.enum) {
        const option = document.createElement("option");
        option.value = String(value);
        option.textContent = String(value);
        select.append(option);
      }
      controls.set(name, { control: select, definition });
      fields.append(label, select);
      continue;
    }
    if (definition.type === "boolean") {
      control.type = "checkbox";
      label.append(control);
      fields.append(label);
    } else {
      if (definition.type !== "object" && definition.type !== "array") {
        control.type = definition.type === "integer" || definition.type === "number" ? "number" : "text";
      }
      if (definition.type === "integer") control.step = "1";
      if (definition.type === "number") control.step = "any";
      if (definition.description) control.placeholder = definition.description;
      control.id = `workflow-input-${name}`;
      label.htmlFor = control.id;
      controls.set(name, { control, definition });
      fields.append(label, control);
    }
    if (definition.type === "boolean") controls.set(name, { control, definition });
  }
  return new Promise(resolve => {
    const cleanup = () => {
      form.removeEventListener("submit", submit);
      byId("workflow-input-cancel").removeEventListener("click", cancel);
      dialog.removeEventListener("cancel", cancel);
    };
    const cancel = event => {
      if (event) event.preventDefault();
      cleanup();
      dialog.close();
      resolve(null);
    };
    const submit = event => {
      event.preventDefault();
      try {
        const values = {};
        for (const [name, { control, definition }] of controls) {
          if (control.type === "checkbox") values[name] = control.checked;
          else if (control.value !== "") {
            if (definition.type === "integer") values[name] = Number.parseInt(control.value, 10);
            else if (definition.type === "number") values[name] = Number(control.value);
            else if (definition.type === "object" || definition.type === "array") values[name] = JSON.parse(control.value);
            else values[name] = control.value;
          }
        }
        cleanup();
        dialog.close();
        resolve(values);
      } catch (error) {
        notify(`Invalid workflow input: ${error.message}`, true);
      }
    };
    form.addEventListener("submit", submit);
    byId("workflow-input-cancel").addEventListener("click", cancel);
    dialog.addEventListener("cancel", cancel);
    dialog.showModal();
  });
}

function makePill(text, status) {
  const pill = document.createElement("span");
  pill.className = `pill status-label ${status ? `status-${status}` : ""}`;
  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = text;
  pill.append(dot, label);
  return pill;
}

function showError(error) {
  notify(error.message || String(error), true);
}

function selectedProject() {
  return state?.projects.find(project => project.name === projectId);
}

function selectedWorkflows() {
  return state?.workflows.filter(workflow => workflow.project === projectId) || [];
}

function activateView(view) {
  const system = view === "system";
  byId("view-runs").classList.toggle("hidden", system);
  byId("view-system").classList.toggle("hidden", !system);
  document.querySelectorAll("[data-view]").forEach(button => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  if (view === "workflows-tab") {
    byId("runs-view").classList.add("hidden");
    byId("workflows-view").classList.remove("hidden");
  } else if (!system) {
    byId("runs-view").classList.remove("hidden");
    byId("workflows-view").classList.add("hidden");
  }
}

document.querySelectorAll("[data-view]").forEach(button => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});

function clearRunDetail() {
  clearInterval(logTimer);
  logTimer = null;
  selectedAttempt = null;
  byId("step-logs").replaceChildren();
  byId("step-logs").classList.add("hidden");
  byId("run-detail").classList.add("hidden");
  byId("run-title").textContent = "Pipeline";
  byId("run-status").replaceChildren();
  byId("run-usage").replaceChildren();
  byId("run-usage").classList.add("hidden");
  byId("run-timing").replaceChildren();
  byId("pipeline").replaceChildren();
  byId("details").textContent = "";
  byId("artifacts").replaceChildren();
}

function renderProjects() {
  const box = byId("projects");
  box.replaceChildren();
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project ${project.name === projectId ? "selected" : ""}`;
    button.addEventListener("click", () => {
      projectId = project.name;
      workflowId = null;
      runId = null;
      clearRunDetail();
      activateView("runs");
      render();
    });
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = project.slug || project.name;
    button.append(name, makePill(project.status, project.status));
    box.append(button);
  }
}

function renderWorkflows() {
  const workflows = selectedWorkflows();
  const sidebar = byId("workflows");
  sidebar.replaceChildren();
  const cards = byId("workflow-cards");
  cards.replaceChildren();
  if (!selectedProject()) {
    const empty = document.createElement("p");
    empty.className = "sidebar-empty";
    empty.textContent = "Select a project.";
    sidebar.append(empty);
    return;
  }
  if (!workflows.length) {
    const empty = document.createElement("p");
    empty.className = "sidebar-empty";
    empty.textContent = "No valid workflows found.";
    sidebar.append(empty);
    const card = document.createElement("p");
    card.className = "muted";
    card.textContent = "Check that the repository contains .aorch/config.yaml, then refresh the project.";
    cards.append(card);
    return;
  }
  for (const workflow of workflows) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `workflow ${workflow.name === workflowId ? "selected" : ""}`;
    const icon = document.createElement("span");
    icon.className = "workflow-dot";
    icon.textContent = "↳";
    const title = document.createElement("span");
    title.textContent = workflow.name;
    button.append(icon, title);
    button.addEventListener("click", () => {
      workflowId = workflow.name;
      runId = null;
      clearRunDetail();
      activateView("runs");
      render();
    });
    sidebar.append(button);

    const card = document.createElement("article");
    card.className = "workflow-card";
    const description = document.createElement("div");
    const heading = document.createElement("h3");
    heading.textContent = workflow.name;
    const detail = document.createElement("p");
    detail.textContent = workflow.trigger?.type === "cron"
      ? `Cron · ${workflow.trigger.cron} (${workflow.trigger.timezone}) · next ${formatTime(workflow.next_due)}`
      : workflow.trigger
        ? `Periodic workflow · every ${workflow.trigger.interval_seconds} seconds · next ${formatTime(workflow.next_due)}`
        : "Manual workflow";
    description.append(heading, detail);
    const choose = makeButton("View runs", async () => {
      workflowId = workflow.name;
      runId = null;
      clearRunDetail();
      activateView("runs");
      render();
    });
    const retention = document.createElement("form");
    retention.className = "retention-form";
    const label = document.createElement("label");
    label.textContent = "Keep completed runs";
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = "10000";
    input.value = String(workflow.retention_count ?? 30);
    input.setAttribute("aria-label", `Completed run retention for ${workflow.name}`);
    const save = makeButton("Save", async () => {}, "button button-secondary");
    save.type = "submit";
    retention.append(label, input, save);
    retention.addEventListener("submit", async event => {
      event.preventDefault();
      await api(`/admin/workflows/${encodeURIComponent(workflow.project)}/${encodeURIComponent(workflow.name)}/retention`, { retention_count: Number(input.value) }, "PUT");
      await refresh();
    });
    card.append(description, retention, choose);
    cards.append(card);
  }
}

function renderProjectHeader() {
  const project = selectedProject();
  const heading = byId("selection-title");
  heading.textContent = project ? `${project.slug || project.name}${workflowId ? ` / ${workflowId}` : ""}` : "Select a project";
  byId("project-source").textContent = project
    ? [project.source_type === "git" ? project.source : project.source, project.ref || "HEAD"].filter(Boolean).join(" · ")
    : "";
  const status = byId("project-status");
  status.replaceChildren();
  const actions = byId("page-actions");
  actions.replaceChildren();
  if (!project) return;

  status.append(makePill(project.status, project.status));
  if (project.commit_sha) status.append(makePill(`commit ${project.commit_sha.slice(0, 12)}`));
  if (project.refreshed) status.append(makePill(`refreshed ${formatTime(project.refreshed)}`));
  if (project.error) {
    const error = document.createElement("span");
    error.className = "status-failed";
    error.textContent = project.error;
    status.append(error);
  }

  if (project.source_type === "git" || project.source_type === "local") {
    const force = project.status === "refreshing";
    const refreshButton = makeButton(force ? "Force refresh" : "Refresh project", async () => {
      refreshButton.disabled = true;
      try {
        const query = force ? "?force=true" : "";
        await api(`/admin/projects/${encodeURIComponent(project.name)}/refresh${query}`, {});
      } finally {
        refreshButton.disabled = false;
      }
      await refresh();
    });
    if (force) refreshButton.title = "Cancel the stalled refresh request and start a new project refresh.";
    actions.append(refreshButton);
  }
  actions.append(makeButton("Variables and secrets", async () => {
    await openEnvironmentDialog(project);
  }));
  actions.append(makeButton("Delete project", async () => {
    const confirmed = window.confirm(`Permanently delete ${project.slug || project.name}, including its workflows, run history, artifacts, and engine Git checkout? Runner workspaces will be kept. Active runs must be cancelled and stopped first.`);
    if (!confirmed) return;
    await api(`/admin/projects/${encodeURIComponent(project.name)}`, undefined, "DELETE");
    projectId = workflowId = runId = null;
    clearRunDetail();
    await refresh();
  }, "button button-danger"));
}

async function openEnvironmentDialog(project) {
  const dialog = byId("environment-dialog");
  const render = async () => {
    const result = await api(`/admin/projects/${encodeURIComponent(project.name)}/environment`);
    const box = byId("environment-items");
    box.replaceChildren();
    if (!result.variables.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No project variables or secrets are configured.";
      box.append(empty);
    }
    for (const item of result.variables) {
      const row = document.createElement("div");
      row.className = "row";
      const label = document.createElement("span");
      label.textContent = `${item.name} · ${item.secret ? "Secret" : "Variable"}`;
      const remove = makeButton("Remove", async () => {
        await api(`/admin/projects/${encodeURIComponent(project.name)}/environment/${encodeURIComponent(item.name)}`, undefined, "DELETE");
        await render();
      }, "button button-danger");
      row.append(label, remove);
      box.append(row);
    }
  };
  await render();
  byId("environment-form").onsubmit = async event => {
    event.preventDefault();
    await api(`/admin/projects/${encodeURIComponent(project.name)}/environment`, {
      name: byId("environment-name").value,
      value: byId("environment-value").value,
      secret: byId("environment-secret").checked
    }, "PUT");
    byId("environment-form").reset();
    await render();
    notify("Project value saved.");
  };
  dialog.showModal();
}

function renderRuns() {
  const box = byId("runs");
  box.replaceChildren();
  const project = selectedProject();
  if (!project || !workflowId) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const icon = document.createElement("span");
    icon.className = "empty-icon";
    icon.textContent = "◷";
    const title = document.createElement("h2");
    title.textContent = project ? "Choose a workflow" : "Select a project";
    const text = document.createElement("p");
    text.textContent = project ? "Select a workflow from the sidebar to inspect and start runs." : "Add or select a project to view its workflows.";
    empty.append(icon, title, text);
    box.append(empty);
    byId("run-count").textContent = "";
    return;
  }

  const runs = state.runs.filter(run => run.project === projectId && run.workflow === workflowId)
    .sort((left, right) => right.created - left.created);
  const workflow = selectedWorkflows().find(item => item.name === workflowId);
  const retentionCount = workflow?.retention_count ?? 30;
  byId("run-count").textContent = String(runs.length);
  const heading = document.createElement("div");
  heading.className = "section-heading";
  const title = document.createElement("h2");
  title.textContent = "Workflow runs";
  const start = makeButton("Run workflow", async () => {
    const inputs = await collectWorkflowInputs(workflow);
    if (inputs === null) return;
    const result = await api("/admin/runs", { project: projectId, workflow: workflowId, inputs });
    runId = result.run_id;
    await refresh();
  }, "button button-primary");
  start.disabled = project.status !== "ready";
  if (start.disabled) start.title = "Refresh the project successfully before creating a run";
  heading.append(title, start);
  box.append(heading);
  const retentionSummary = document.createElement("p");
  retentionSummary.className = "retention-summary";
  const retainedCount = runs.filter(run => run.keep_forever).length;
  retentionSummary.textContent = `Keeps the newest ${retentionCount} completed runs. ${retainedCount} run${retainedCount === 1 ? "" : "s"} protected forever.`;
  box.append(retentionSummary);
  if (!runs.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const title = document.createElement("h2");
    title.textContent = "No runs yet";
    const text = document.createElement("p");
    text.textContent = "Start this workflow to see its progress here.";
    empty.append(title, text);
    box.append(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "run-list";
  const header = document.createElement("div");
  header.className = "run-list-header";
  for (const value of ["Run", "Status", "Started", "Duration", "Actions"]) {
    const cell = document.createElement("span");
    cell.textContent = value;
    header.append(cell);
  }
  list.append(header);
  for (const run of runs) {
    const row = document.createElement("div");
    row.className = "run-row";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "run-open";
    open.textContent = `Run ${run.id.slice(0, 8)}`;
    open.addEventListener("click", () => detail(run.id).catch(showError));
    const status = makePill(run.status, run.status);
    const created = document.createElement("span");
    created.className = "run-meta run-created";
    created.textContent = formatTime(run.created);
    const duration = document.createElement("span");
    duration.className = "run-meta run-duration";
    if (run.finished || activeStatuses.has(run.status)) {
      duration.dataset.durationStart = run.created;
      if (run.finished) duration.dataset.durationEnd = run.finished;
    }
    duration.textContent = run.finished
      ? formatDuration(Math.max(0, run.finished - run.created))
      : activeStatuses.has(run.status)
        ? formatDuration(Math.max(0, Date.now() / 1000 - run.created))
        : "Unavailable";
    const controls = document.createElement("div");
    controls.className = "run-controls";
    if (run.blocked_reason) controls.append(makePill(run.blocked_reason));
    if (run.keep_forever) controls.append(makePill(run.retention_pending ? "Keep forever pending" : "Keep forever"));
    controls.append(makeButton(run.keep_forever ? "Unkeep" : "Keep forever", async () => {
      if (run.keep_forever) {
        const eligible = runs.filter(item => (!item.keep_forever || item.id === run.id) && ["succeeded", "failed", "cancelled"].includes(item.status));
        const position = eligible.findIndex(item => item.id === run.id);
        if (position >= retentionCount && !window.confirm("This run is older than the retention limit and may be deleted during the next cleanup. Continue?")) return;
      }
      await api(`/admin/runs/${run.id}/retention`, { keep_forever: !run.keep_forever }, "PUT");
      await refresh();
    }));
    if (activeStatuses.has(run.status)) {
      controls.append(makeButton("Cancel", async () => {
        await api(`/admin/runs/${run.id}/cancel`, {});
        await refresh();
      }));
    } else {
      const remove = makeButton("Delete", async () => {
        await api(`/admin/runs/${run.id}`, undefined, "DELETE");
        if (runId === run.id) { runId = null; clearRunDetail(); }
        await refresh();
      }, "button button-danger");
      remove.disabled = Boolean(run.keep_forever);
      if (run.keep_forever) remove.title = "Remove Keep forever before deleting this run";
      controls.append(remove);
    }
    row.append(open, status, created, duration, controls);
    list.append(row);
  }
  box.append(list);
}

function topologicalLevels(steps) {
  const pending = new Set(Object.keys(steps));
  const levels = {};
  while (pending.size) {
    let progressed = false;
    for (const name of [...pending]) {
      const needs = (steps[name].needs || []).filter(dependency => steps[dependency]);
      if (needs.every(dependency => !pending.has(dependency))) {
        levels[name] = needs.length ? Math.max(...needs.map(dependency => levels[dependency] || 0)) + 1 : 0;
        pending.delete(name);
        progressed = true;
      }
    }
    if (!progressed) {
      for (const name of pending) levels[name] = 0;
      break;
    }
  }
  return levels;
}

function drawEdges(canvas, nodes, definitions, levels) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("graph-edges");
  const width = canvas.scrollWidth;
  const height = canvas.scrollHeight;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const bounds = canvas.getBoundingClientRect();
  for (const [name, definition] of Object.entries(definitions)) {
    for (const dependency of definition.needs || []) {
      if (!nodes.has(dependency) || !nodes.has(name)) continue;
      const from = nodes.get(dependency).getBoundingClientRect();
      const to = nodes.get(name).getBoundingClientRect();
      const x1 = from.right - bounds.left;
      const y1 = from.top + from.height / 2 - bounds.top;
      const x2 = to.left - bounds.left;
      const y2 = to.top + to.height / 2 - bounds.top;
      const middle = (x1 + x2) / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "var(--border)");
      path.setAttribute("stroke-width", "1.5");
      svg.append(path);
    }
  }
  canvas.prepend(svg);
}

function renderPipeline(detailData) {
  const pipeline = byId("pipeline");
  pipeline.replaceChildren();
  const definition = detailData.definition || {};
  const steps = definition.steps || {};
  const levels = topologicalLevels(steps);
  const maxLevel = Math.max(0, ...Object.values(levels));
  const canvas = document.createElement("div");
  canvas.className = "graph-canvas";
  const columns = document.createElement("div");
  columns.className = "graph-columns";
  columns.style.gridTemplateColumns = `repeat(${maxLevel + 1}, minmax(190px, 1fr))`;
  const nodes = new Map();
  const statusByName = Object.fromEntries((detailData.steps || []).map(step => [step.name, step.status]));
  const attemptsByStep = (detailData.attempts || []).reduce((groups, attempt) => ((groups[attempt.step] ||= []).push(attempt), groups), {});
  for (let level = 0; level <= maxLevel; level += 1) {
    const column = document.createElement("div");
    column.className = "graph-column";
    for (const [name, step] of Object.entries(steps)) {
      if (levels[name] !== level) continue;
      const node = document.createElement("article");
      node.className = "node";
      node.dataset.step = name;
      node.setAttribute("role", "button");
      node.tabIndex = 0;
      node.setAttribute("aria-label", `Open logs for ${name}`);
      const heading = document.createElement("b");
      heading.textContent = name;
      const status = makePill(statusByName[name] || "pending", statusByName[name] || "pending");
      const description = document.createElement("small");
      description.textContent = `${step.type}${step.when ? " · conditional" : ""}${step.repeat ? " · repeat" : ""}`;
      node.append(heading, status, description);
      if (step.needs?.length) {
        const needs = document.createElement("small");
        needs.textContent = `Depends on ${step.needs.join(", ")}`;
        node.append(needs);
      }
      const attempts = attemptsByStep[name] || [];
      const measured = attempts.filter(attempt => Number.isFinite(attempt.duration_seconds));
      if (measured.length) {
        const totalSeconds = measured.reduce((total, attempt) => total + attempt.duration_seconds, 0);
        const elapsed = document.createElement("small");
        const current = statusByName[name] === "running" || statusByName[name] === "reconciling";
        elapsed.className = "node-duration";
        elapsed.textContent = measured.length > 1
          ? `Total ${formatDuration(totalSeconds)} across ${measured.length} attempts`
          : `${current ? "Running for" : "Duration"} ${formatDuration(totalSeconds)}`;
        node.append(elapsed);
      }
      node.addEventListener("click", () => openStepLogs(name, attempts).catch(showError));
      node.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openStepLogs(name, attempts).catch(showError);
        }
      });
      column.append(node);
      nodes.set(name, node);
    }
    columns.append(column);
  }
  canvas.append(columns);
  pipeline.append(canvas);
  requestAnimationFrame(() => drawEdges(canvas, nodes, steps, levels));
}

async function openStepLogs(step, attempts) {
  clearInterval(logTimer);
  const panel = byId("step-logs");
  panel.replaceChildren();
  panel.classList.remove("hidden");
  if (!attempts.length) {
    const title = document.createElement("h3");
    title.textContent = `Logs · ${step}`;
    const message = document.createElement("p");
    message.className = "muted";
    message.textContent = "This step has no execution attempts yet.";
    panel.append(title, message);
    selectedAttempt = null;
    return;
  }
  selectedAttempt = attempts.at(-1)?.id;
  const heading = document.createElement("div");
  heading.className = "section-heading";
  const title = document.createElement("h3");
  title.textContent = `Logs · ${step}`;
  const selector = document.createElement("select");
  selector.setAttribute("aria-label", "Execution attempt");
  for (const attempt of attempts) {
    const option = document.createElement("option");
    option.value = attempt.id;
    option.textContent = `Attempt ${attempt.generation} · ${attempt.status}`;
    selector.append(option);
  }
  selector.value = selectedAttempt;
  const mode = document.createElement("select");
  mode.setAttribute("aria-label", "Log view");
  for (const value of ["activity", "raw"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "activity" ? "Activity" : "Raw";
    mode.append(option);
  }
  heading.append(title, selector, mode);
  const content = document.createElement("pre");
  content.className = "step-log-output";
  const status = document.createElement("p");
  status.className = "muted";
  const download = makeButton("Download current log", async () => {
    const parts = [];
    let downloadCursor = 0;
    let complete = false;
    while (!complete) {
      const query = new URLSearchParams({ after: String(downloadCursor), limit: "16", view: mode.value });
      const page = await api(`/admin/runs/${runId}/attempts/${selector.value}/logs?${query}`);
      for (const event of page.events || []) {
        parts.push(`[${formatTime(event.time)}] ${mode.value === "raw" ? JSON.stringify(event) : event.message || ""}\n`);
        downloadCursor = event.sequence;
      }
      complete = !page.available || page.complete || !page.events?.length;
    }
    const blob = new Blob(parts, { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `aorch-${step}-${selectedAttempt}.log`;
    link.click();
    URL.revokeObjectURL(link.href);
  });
  panel.append(heading, status, download, content);
  let cursor = 0;
  let loading = false;
  const load = async () => {
    if (loading) return true;
    loading = true;
    try {
    const attemptId = selector.value;
    if (attemptId !== selectedAttempt) { selectedAttempt = attemptId; cursor = 0; content.textContent = ""; }
    const query = new URLSearchParams({ after: String(cursor), limit: "16", view: mode.value });
    const data = await api(`/admin/runs/${runId}/attempts/${attemptId}/logs?${query}`);
    status.textContent = data.message || (data.truncated ? "Log limit reached, remaining output was discarded." : data.complete ? "Attempt complete" : "Waiting for new log entries…");
    for (const event of data.events || []) {
      const line = `[${formatTime(event.time)}] ${mode.value === "raw" ? JSON.stringify(event) : event.message || JSON.stringify(event.data || {})}\n`;
      content.textContent += line;
      cursor = event.sequence;
    }
    if (content.textContent.length > 1024 * 1024) {
      content.textContent = content.textContent.slice(-1024 * 1024);
      status.textContent = `Showing the latest 1 MiB. ${status.textContent}`;
    }
    content.scrollTop = content.scrollHeight;
    return Boolean(data.available && !data.complete);
    } finally {
      loading = false;
    }
  };
  selector.addEventListener("change", () => { cursor = 0; content.textContent = ""; load().catch(showError); });
  mode.addEventListener("change", () => { cursor = 0; content.textContent = ""; load().catch(showError); });
  const active = await load();
  if (active) logTimer = setInterval(async () => {
    const stillActive = await load();
    if (!stillActive) { clearInterval(logTimer); logTimer = null; }
  }, 2000);
}

function renderActivity(detailData) {
  const box = byId("event-log");
  box.replaceChildren();
  const entries = [];
  for (const event of detailData.events || []) {
    let payload = event.payload || {};
    if (typeof payload === "string") {
      try { payload = JSON.parse(payload); } catch (_) { payload = { text: payload }; }
    }
    const data = payload.data || payload;
    const message = data.text || data.message || data.summary || data.stderr || data.stdout || "";
    entries.push({
      time: event.created,
      kind: event.kind,
      message: typeof message === "string" ? message : JSON.stringify(message)
    });
  }
  for (const attempt of detailData.attempts || []) {
    let result = attempt.result || {};
    if (typeof result === "string") {
      try { result = JSON.parse(result); } catch (_) { result = { summary: result }; }
    }
    if (result.summary || result.error) {
      entries.push({
        time: null,
        kind: `Step · ${attempt.step} · ${attempt.status}`,
        message: result.summary || result.error
      });
    }
  }
  entries.sort((left, right) => (left.time || 0) - (right.time || 0));
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "event-empty";
    empty.textContent = "No activity has been recorded for this run yet.";
    box.append(empty);
    return;
  }
  for (const entry of entries) {
    const row = document.createElement("article");
    row.className = "event-row";
    const time = document.createElement("time");
    time.textContent = formatTime(entry.time);
    const kind = document.createElement("span");
    kind.className = "event-kind";
    kind.textContent = entry.kind;
    const message = document.createElement("span");
    message.className = "event-message";
    message.textContent = entry.message;
    row.append(time, kind, message);
    box.append(row);
  }
}

function renderApprovals(detailData) {
  const panel = byId("approval-actions");
  panel.replaceChildren();
  const pending = (detailData.steps || []).filter(step => step.status === "waiting_approval");
  panel.classList.toggle("hidden", pending.length === 0);
  if (!pending.length) return;
  const heading = document.createElement("h3");
  heading.textContent = "Approval required";
  panel.append(heading);
  for (const step of pending) {
    const result = typeof step.result === "string" ? JSON.parse(step.result) : (step.result || {});
    const card = document.createElement("article");
    card.className = "approval-card";
    const summary = document.createElement("p");
    summary.textContent = result.summary || "Review the verified change before publication.";
    const digest = document.createElement("code");
    digest.textContent = `Verified content SHA-256: ${result.outputs?.content_fingerprint || "unavailable"}`;
    const evidence = (detailData.artifacts || []).find(item => item.path === "approval.diff");
    const artifactLink = evidence ? document.createElement("a") : null;
    if (artifactLink) {
      artifactLink.href = `/admin/artifacts/${encodeURIComponent(evidence.id)}`;
      artifactLink.textContent = "Download verified diff";
    }
    const note = document.createElement("textarea");
    note.maxLength = 4000;
    note.setAttribute("aria-label", "Approval note");
    note.placeholder = "Optional note";
    const actions = document.createElement("div");
    actions.className = "approval-buttons";
    for (const [decision, label] of [["approved", "Approve"], ["rejected", "Reject"]]) {
      const button = makeButton(label, async () => {
        await api(`/admin/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(step.name)}/approval`, {
          decision,
          note: note.value
        });
        await refresh();
        if (runId) await detail(runId);
        notify(decision === "approved" ? "Change approved." : "Change rejected.");
      }, decision === "approved" ? "button button-primary" : "button button-secondary");
      button.disabled = !["owner", "admin"].includes(activeOrganization?.role);
      actions.append(button);
    }
    card.append(summary, digest);
    if (artifactLink) card.append(artifactLink);
    card.append(note, actions);
    panel.append(card);
  }
}

function renderQuestions(detailData) {
  const panel = byId("approval-actions");
  const questions = [];
  for (const step of detailData.steps || []) {
    if (step.status !== "needs_input" || !step.result) continue;
    const result = typeof step.result === "string" ? JSON.parse(step.result) : step.result;
    for (const item of result.outputs?.ambiguities || []) {
      if (item && typeof item.id === "string" && typeof item.question === "string") {
        questions.push(item);
      }
    }
  }
  if (!questions.length) return;
  panel.classList.remove("hidden");
  const heading = document.createElement("h3");
  heading.textContent = "More information needed";
  const form = document.createElement("form");
  form.className = "approval-card";
  const fields = new Map();
  for (const question of questions) {
    if (fields.has(question.id)) continue;
    const label = document.createElement("label");
    label.textContent = question.question;
    const input = document.createElement("textarea");
    input.required = true;
    input.maxLength = 10000;
    input.setAttribute("aria-label", question.question);
    fields.set(question.id, input);
    form.append(label, input);
  }
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "button button-primary";
  submit.textContent = "Submit answers and continue";
  form.append(submit);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const answers = Object.fromEntries([...fields].map(([id, input]) => [id, input.value]));
    const resumed = await api(`/admin/runs/${encodeURIComponent(runId)}/answers`, { answers });
    runId = resumed.run_id;
    await refresh();
    notify("Answers submitted. A new run is queued with a fresh project snapshot.");
  });
  panel.append(heading, form);
}

function renderUsage(detailData) {
  const panel = byId("run-usage");
  panel.replaceChildren();
  const usage = [];
  for (const step of detailData.steps || []) {
    let result = step.result;
    if (typeof result === "string") {
      try { result = JSON.parse(result); } catch (_) { result = null; }
    }
    const reported = result?.outputs?.api_usage;
    if (reported && typeof reported === "object") usage.push({ step: step.name, ...reported });
  }
  if (!usage.length) return;
  const heading = document.createElement("h3");
  heading.textContent = "Reported model usage";
  panel.append(heading);
  let totalTokens = 0;
  let totalCost = 0;
  let tokensKnown = true;
  let costsKnown = true;
  for (const item of usage) {
    if (Number.isFinite(item.tokens)) totalTokens += item.tokens;
    else tokensKnown = false;
    if (Number.isFinite(item.estimated_cost_usd)) totalCost += item.estimated_cost_usd;
    else costsKnown = false;
    const line = document.createElement("p");
    const tokens = Number.isFinite(item.tokens) ? `${item.tokens.toLocaleString()} tokens` : "token count unavailable";
    const cost = Number.isFinite(item.estimated_cost_usd) ? `, estimated $${item.estimated_cost_usd.toFixed(4)}` : ", cost unavailable";
    line.textContent = `${item.step}: ${tokens}${cost}`;
    panel.append(line);
  }
  const note = document.createElement("p");
  note.className = "muted";
  const tokens = tokensKnown ? `${totalTokens.toLocaleString()} reported tokens` : "Some token usage is unavailable";
  const cost = costsKnown ? `, estimated total $${totalCost.toFixed(4)}` : ", total cost unavailable";
  note.textContent = `${tokens}${cost}. CLI subscription usage is not reported by this view.`;
  panel.append(note);
  panel.classList.remove("hidden");
}

async function detail(id) {
  runId = id;
  const data = await api(`/admin/runs/${id}`);
  byId("run-detail").classList.remove("hidden");
  byId("run-title").textContent = `Run ${id.slice(0, 8)}`;
  byId("run-status").replaceChildren(makePill(data.status, data.status));
  const timing = byId("run-timing");
  timing.replaceChildren();
  const timingItems = [
    ["Total duration", data.run_seconds],
    ["Execution time", data.execution_seconds],
    ["Waiting time", data.waiting_seconds],
    ["Reconciliation", data.reconciliation_seconds]
  ];
  for (const [labelText, seconds] of timingItems) {
    const item = document.createElement("div");
    item.className = "run-timing-item";
    const label = document.createElement("span");
    label.className = "muted";
    label.textContent = labelText;
    const value = document.createElement("b");
    value.textContent = Number.isFinite(seconds) ? formatDuration(seconds) : "Unavailable";
    if (labelText === "Total duration" && !data.finished) {
      value.dataset.durationStart = data.created;
    }
    if (labelText === "Execution time" && Number.isFinite(seconds) && data.attempts.some(attempt => attempt.status === "running" && Number.isFinite(attempt.execution_seconds))) {
      value.dataset.durationBase = seconds;
      value.dataset.durationSince = data.measured_at;
    }
    item.append(label, value);
    timing.append(item);
  }
  renderPipeline(data);
  renderUsage(data);
  renderActivity(data);
  renderApprovals(data);
  renderQuestions(data);
  byId("details").textContent = JSON.stringify({ repository: data.repository, timing: { total_seconds: data.run_seconds, execution_seconds: data.execution_seconds, waiting_seconds: data.waiting_seconds, reconciliation_seconds: data.reconciliation_seconds }, steps: data.steps, attempts: data.attempts, events: data.events }, null, 2);
  const artifacts = byId("artifacts");
  artifacts.replaceChildren();
  for (const artifact of data.artifacts || []) {
    const link = document.createElement("a");
    link.href = `/admin/artifacts/${encodeURIComponent(artifact.id)}`;
    link.textContent = `Download artifact ${artifact.id}`;
    artifacts.append(link);
  }
}

function renderTable(containerId, rows, columns) {
  const box = byId(containerId);
  box.replaceChildren();
  const countId = containerId === "runners" ? "runner-count" : containerId === "resources" ? "resource-count" : null;
  if (countId) byId(countId).textContent = String(rows.length);
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "None";
    box.append(empty);
    return;
  }
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  for (const column of columns) {
    const cell = document.createElement("th");
    cell.textContent = column;
    head.append(cell);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const column of columns) {
      const cell = tr.insertCell();
      if (column === "action") {
        if (row.revoked) {
          cell.textContent = "Revoked";
        } else {
          const access = document.createElement("button");
          access.className = "button button-secondary";
          access.type = "button";
          access.textContent = "Projects";
          access.addEventListener("click", () => openRunnerAccess(row).catch(showError));
          const button = document.createElement("button");
          button.className = "button button-secondary";
          button.type = "button";
          button.textContent = "Revoke";
          button.addEventListener("click", async () => {
            if (!window.confirm("Revoke runner " + row.id + "? It will stop accepting new work.")) return;
            try {
              await api("/admin/runners/" + encodeURIComponent(row.id), {}, "DELETE");
              await refresh();
            } catch (error) { showError(error); }
          });
          cell.append(access, button);
        }
      } else {
        cell.textContent = column === "projects"
          ? JSON.parse(row.projects || "[]").map(name => state.projects.find(project => project.name === name)?.slug || name).join(", ")
          : ["heartbeat", "created", "delivered"].includes(column) ? formatTime(row[column]) :
          column === "revoked" ? (row.revoked ? "Yes" : "No") : String(row[column] ?? "");
      }
    }
  }
  box.append(table);
}

async function openRunnerAccess(runner) {
  runnerAccessId = runner.id;
  byId("runner-access-title").textContent = `Projects for ${runner.name || runner.id}`;
  const box = byId("runner-access-projects");
  box.replaceChildren();
  const assigned = new Set(JSON.parse(runner.projects || "[]"));
  for (const project of state.projects) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = project.name;
    checkbox.checked = assigned.has(project.name);
    label.append(checkbox, document.createTextNode(project.slug || project.name));
    box.append(label);
  }
  byId("runner-access-dialog").showModal();
}

function render() {
  if (!state) return;
  byId("errors").textContent = JSON.stringify(state.configuration_errors, null, 2);
  renderProjects();
  renderWorkflows();
  renderProjectHeader();
  renderRuns();
  const runnerColumns = ["name", "id", "projects", "online", "capacity", "heartbeat", "revoked"];
  if (["owner", "admin"].includes(activeOrganization?.role)) runnerColumns.push("action");
  renderTable("runners", state.runners, runnerColumns);
  renderTable("resources", state.resource_locks, ["resource", "run", "runner"]);
  renderTable("notifications", state.notifications || [], ["event", "run", "attempts", "delivered", "error"]);
  renderNotificationSettings();
}

function renderNotificationSettings() {
  const panel = byId("notification-settings");
  const isAdmin = ["owner", "admin"].includes(activeOrganization?.role);
  panel.classList.toggle("hidden", !isAdmin);
  const kind = byId("notification-kind").value;
  const configured = Boolean(state?.notification_targets?.[kind]);
  byId("notification-target-status").textContent = configured
    ? `${kind === "slack" ? "Slack" : "Generic webhook"} endpoint is configured.`
    : "No endpoint is configured for this channel.";
  byId("notification-remove").disabled = !configured;
  const labels = {
    "run.failed": "Run failed", "run.needs_input": "Run needs an answer",
    "approval.requested": "Approval requested", "run.needs_attention": "Run needs attention",
    "run.reconciling": "External result needs reconciliation", "run.succeeded": "Run succeeded"
  };
  const preferences = state?.notification_events || {};
  const fieldset = byId("notification-event-settings");
  fieldset.replaceChildren(Object.assign(document.createElement("legend"), { textContent: "Notify me about" }));
  for (const [event, label] of Object.entries(labels)) {
    const wrapper = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = event;
    input.checked = preferences[event] !== false;
    wrapper.append(input, document.createTextNode(label));
    fieldset.append(wrapper);
  }
}

async function refresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    state = await api("/admin/state");
    csrf = state.csrf;
    const account = await api("/auth/me");
    state.user = account.user;
    organizations = account.organizations;
    activeOrganization = organizations.find(org => org.id === account.organization_id);
    const organizationSelect = byId("organization-select");
    organizationSelect.replaceChildren();
    for (const organization of organizations) {
      const option = document.createElement("option");
      option.value = organization.id;
      option.textContent = organization.slug;
      option.title = organization.id;
      option.selected = organization.id === account.organization_id;
      organizationSelect.append(option);
    }
    organizationSelect.classList.remove("hidden");
    byId("add-organization").classList.remove("hidden");
    byId("invite-member").classList.toggle("hidden", !["owner", "admin"].includes(activeOrganization?.role));
    byId("add-runner").classList.toggle("hidden", !["owner", "admin"].includes(activeOrganization?.role));
    byId("google-login").href = "/auth/google/start?link=1";
    byId("github-login").href = "/auth/github/start?link=1";
    byId("google-login").textContent = "Link Google";
    byId("github-login").textContent = "Link GitHub";
    byId("login").classList.add("hidden");
    byId("logout").classList.remove("hidden");
    byId("local-auth-label").classList.toggle("hidden", !account.local_auth);
    byId("main").classList.remove("hidden");
    if (!projectId && state.projects.length) projectId = state.projects[0].name;
    if (projectId && !state.projects.some(project => project.name === projectId)) {
      projectId = state.projects[0]?.name || null;
      workflowId = runId = null;
    }
    if (workflowId && !state.workflows.some(workflow => workflow.project === projectId && workflow.name === workflowId)) workflowId = null;
    if (runId && !state.runs.some(run => run.id === runId)) { runId = null; clearRunDetail(); }
    notify("Connected");
    render();
    if (runId) await detail(runId);
    if (!refreshTimer) refreshTimer = window.setInterval(() => refresh().catch(showError), 15000);
    if (!durationTimer) durationTimer = window.setInterval(updateDurationLabels, 1000);
    if (!eventStream) {
      eventStream = new EventSource("/admin/events");
      eventStream.onmessage = () => {
        window.clearTimeout(eventRefresh);
        eventRefresh = window.setTimeout(() => refresh().catch(showError), 180);
      };
      eventStream.onerror = () => notify("Connection interrupted. Reconnecting.");
    }
  })();
  try { await refreshPromise; } finally { refreshPromise = null; }
}

byId("login").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const invitationToken = new URLSearchParams(window.location.search).get("invite_token");
    await api("/auth/email/request", { email: byId("email").value, invitation_token: invitationToken || "" });
    notify("If the address can sign in, a one-time link has been sent.");
  } catch (error) { showError(error); }
});

byId("local-login").addEventListener("click", async () => {
  try {
    const result = await api("/auth/local", {});
    csrf = result.csrf;
    await refresh();
  } catch (error) { showError(error); }
});

byId("organization-select").addEventListener("change", async event => {
  try {
    await api("/auth/organization/switch", { organization_id: event.target.value });
    projectId = workflowId = runId = null;
    await refresh();
  } catch (error) { showError(error); }
});

byId("add-organization").addEventListener("click", async () => {
  const slug = window.prompt("Organization name");
  if (!slug) return;
  try {
    const created = await api("/auth/organizations", { slug });
    await api("/auth/organization/switch", { organization_id: created.id });
    await refresh();
    notify("Organization created.");
  } catch (error) { showError(error); }
});

async function renderOrganizationDialog() {
  const members = await api(`/auth/organizations/${encodeURIComponent(activeOrganization.id)}/members`);
  const invitations = await api(`/auth/organizations/${encodeURIComponent(activeOrganization.id)}/invitations`);
  const memberBox = byId("organization-members");
  memberBox.replaceChildren();
  for (const member of members) {
    const row = document.createElement("div");
    row.className = "organization-row";
    const details = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = member.display_name || member.username;
    const email = document.createElement("span");
    email.textContent = member.email || member.username;
    details.append(title, email);
    const controls = document.createElement("div");
    const role = document.createElement("select");
    for (const value of ["owner", "admin", "member"]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      option.selected = value === member.role;
      role.append(option);
    }
    role.disabled = activeOrganization.role !== "owner" || member.id === (state.user?.id);
    role.addEventListener("change", async () => {
      await api(`/auth/organizations/${encodeURIComponent(activeOrganization.id)}/members/${encodeURIComponent(member.id)}`, { role: role.value }, "PUT");
      await renderOrganizationDialog();
    });
    const remove = makeButton("Remove", async () => {
      if (!window.confirm(`Remove ${member.username} from this organization?`)) return;
      await api(`/auth/organizations/${encodeURIComponent(activeOrganization.id)}/members/${encodeURIComponent(member.id)}`, {}, "DELETE");
      await renderOrganizationDialog();
    }, "button button-danger");
    remove.disabled = activeOrganization.role !== "owner" && member.role !== "member";
    controls.append(role, remove);
    row.append(details, controls);
    memberBox.append(row);
  }
  const inviteBox = byId("organization-invitations");
  inviteBox.replaceChildren();
  for (const invitation of invitations) {
    const row = document.createElement("div");
    row.className = "organization-row";
    const label = document.createElement("span");
    label.textContent = `${invitation.email} · ${invitation.role}`;
    const revoke = makeButton("Revoke", async () => {
      await api(`/auth/organizations/${encodeURIComponent(activeOrganization.id)}/invitations/${encodeURIComponent(invitation.id)}`, {}, "DELETE");
      await renderOrganizationDialog();
    });
    row.append(label, revoke);
    inviteBox.append(row);
  }
  byId("invite-role").disabled = activeOrganization.role !== "owner";
}

byId("invite-member").addEventListener("click", async () => {
  try {
    await renderOrganizationDialog();
    byId("organization-dialog").showModal();
  } catch (error) { showError(error); }
});

byId("invite-form").addEventListener("submit", async event => {
  event.preventDefault();
  const email = byId("invite-email").value;
  const role = byId("invite-role").value;
  try {
    await api(`/auth/organizations/${encodeURIComponent(activeOrganization.id)}/invitations`, { email, role });
    byId("invite-form").reset();
    await renderOrganizationDialog();
    notify(`Invitation sent to ${email}.`);
  } catch (error) { showError(error); }
});

byId("add-runner").addEventListener("click", () => {
  const box = byId("runner-projects");
  box.replaceChildren();
  for (const project of state.projects) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = project.name;
    checkbox.checked = project.name === projectId;
    label.append(checkbox, document.createTextNode(project.name));
    box.append(label);
  }
  byId("runner-command").classList.add("hidden");
  byId("runner-copy").classList.add("hidden");
  byId("runner-expiry").classList.add("hidden");
  byId("runner-dialog").showModal();
});

byId("runner-form").addEventListener("submit", async event => {
  event.preventDefault();
  const projects = Array.from(byId("runner-projects").querySelectorAll("input:checked"), input => input.value);
  try {
    const enrollment = await api("/admin/runners", {
      name: byId("runner-name").value.trim(),
      capacity: Number(byId("runner-capacity").value),
      projects
    });
    byId("runner-command").value = enrollment.command;
    byId("runner-command").classList.remove("hidden");
    byId("runner-copy").classList.remove("hidden");
    byId("runner-expiry").textContent = "This one-time command expires in one hour. Copy it now and run it in a terminal on the runner machine.";
    byId("runner-expiry").classList.remove("hidden");
  } catch (error) { showError(error); }
});

byId("runner-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(byId("runner-command").value);
    notify("Runner setup command copied.");
  } catch (error) { showError(error); }
});

byId("runner-access-form").addEventListener("submit", async event => {
  event.preventDefault();
  const projects = Array.from(
    byId("runner-access-projects").querySelectorAll("input:checked"),
    input => input.value
  );
  try {
    await api(`/admin/runners/${encodeURIComponent(runnerAccessId)}/projects`, { projects }, "PUT");
    byId("runner-access-dialog").close();
    await refresh();
    notify("Runner project access updated.");
  } catch (error) { showError(error); }
});

async function prepareAuth() {
  const providers = await api("/auth/providers");
  byId("login").classList.toggle("hidden", !providers.email);
  byId("local-login").classList.toggle("hidden", !providers.local);
  byId("google-login").classList.toggle("hidden", !providers.google);
  byId("github-login").classList.toggle("hidden", !providers.github);
  const params = new URLSearchParams(window.location.search);
  const invitationToken = params.get("invite_token");
  if (invitationToken) {
    const suffix = `?invitation_token=${encodeURIComponent(invitationToken)}`;
    byId("google-login").href = `/auth/google/start${suffix}`;
    byId("github-login").href = `/auth/github/start${suffix}`;
  }
  const token = params.get("login_token");
  if (token) {
    window.history.replaceState({}, "", "/");
    try {
      let confirmed = await api("/auth/email/confirm", { token });
      if (confirmed.needs_onboarding) {
        const username = window.prompt("Choose a username for your Runestra account");
        if (!username) {
          notify("Sign-in stopped. Reload the page to use the link again.", true);
          return;
        }
        const organization = window.prompt("Choose your default organization name", username);
        if (!organization) {
          notify("Sign-in stopped. Reload the page to use the link again.", true);
          return;
        }
        confirmed = await api("/auth/email/confirm", {
          token, username, display_name: username, organization
        });
      }
      csrf = confirmed.csrf;
      const invitation = params.get("invite_token");
      if (invitation) {
        const accepted = await api("/auth/invitations/accept", { token: invitation });
        await api("/auth/organization/switch", { organization_id: accepted.organization_id });
      }
      notify("Signed in. Loading your organization...");
      await refresh();
    } catch (error) { showError(error); }
  }
  const invitation = params.get("invite_token");
  if (invitation) {
    try {
      const account = await api("/auth/me");
      csrf = account.csrf;
      const accepted = await api("/auth/invitations/accept", { token: invitation });
      await api("/auth/organization/switch", { organization_id: accepted.organization_id });
      window.history.replaceState({}, "", "/");
      await refresh();
      notify("Organization invitation accepted.");
    } catch (_) {
      notify("Sign in using the invited email, then open the invitation link again.");
    }
  }
}

prepareAuth().catch(showError);

byId("logout").addEventListener("click", async () => {
  try {
    await api("/logout", {});
    byId("google-login").href = "/auth/google/start";
    byId("github-login").href = "/auth/github/start";
    byId("google-login").textContent = "Google";
    byId("github-login").textContent = "GitHub";
    eventStream?.close();
    eventStream = null;
    window.clearInterval(refreshTimer);
    refreshTimer = null;
    state = null;
    byId("main").classList.add("hidden");
    byId("organization-select").classList.add("hidden");
    byId("add-organization").classList.add("hidden");
    byId("invite-member").classList.add("hidden");
    byId("logout").classList.add("hidden");
    byId("login").classList.remove("hidden");
    notify("Signed out");
  } catch (error) { showError(error); }
});

byId("refresh").addEventListener("click", () => refresh().catch(showError));
byId("reload").addEventListener("click", async () => {
  try { await api("/admin/reload", {}); await refresh(); }
  catch (error) { showError(error); }
});
byId("add-project-toggle").addEventListener("click", () => {
  byId("add-project").classList.toggle("hidden");
});
byId("add-project").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const name = byId("project-name").value;
    const result = await api("/admin/projects", {
      name,
      git: byId("project-git").value,
      ref: byId("project-ref").value || "HEAD"
    });
    projectId = result.project_id;
    workflowId = null;
    byId("add-project").reset();
    byId("add-project").classList.add("hidden");
    await refresh();
    if (result.error) showError(new Error(result.error));
    else if (result.status === "pending") notify("Project created. Assign an online runner, then refresh the project.");
  } catch (error) { showError(error); }
});

refresh().catch(() => { /* Authentication is expected to be absent before sign in. */ });
