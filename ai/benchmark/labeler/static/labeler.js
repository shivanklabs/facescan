// FaceScan labeler page. Plain JavaScript, no build step. All data is inserted as text
// (textContent / value), never as HTML.
"use strict";

const PERSON_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
let state = null;
let view = "clusters";
let card = 0; // current cluster / photo index in the current view
let face = 0; // current face index inside the current card
let lastPerson = "";

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else if (k === "checked") node.checked = Boolean(v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function status(msg) { document.getElementById("status").textContent = msg; }

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) { status(`Error ${res.status} on ${path}`); return false; }
  status("Saved.");
  return true;
}

async function load() {
  const res = await fetch("/api/state");
  state = await res.json();
  document.getElementById("event").textContent = `(${state.event})`;
  document.getElementById("selfies").textContent =
    state.selfie_persons.length ? state.selfie_persons.join(", ") : "none";
  document.getElementById("progress").textContent =
    `${state.progress.complete} of ${state.progress.total} photos fully labelled`;
  document.getElementById("undo").disabled = state.undo_steps === 0;
  render();
}

function askPerson(question) {
  const value = (window.prompt(question, lastPerson) || "").trim().toLowerCase();
  if (!value) return null;
  if (value !== "unknown" && !PERSON_RE.test(value)) { status("Invalid id: use a-z, 0-9, _ or -"); return null; }
  if (value !== "unknown") lastPerson = value;
  return value;
}

// ---- the cards of the current view: [{title, faces, cluster?, photo?}] ----
function cards() {
  if (!state) return [];
  if (view === "clusters") {
    return state.clusters.map((c) => ({ cluster: c, faces: c.faces }));
  }
  if (view === "unclustered") return [{ faces: state.unclustered }];
  return state.photos.map((p) => ({ photo: p, faces: p.faces }));
}

function currentFaceRow() {
  const c = cards()[card];
  return c && c.faces.length ? c.faces[Math.min(face, c.faces.length - 1)] : null;
}

// ---- actions shared by buttons and shortcuts ----
async function labelFace(row, label) {
  if (await api(`/api/faces/${row}/label`, { label })) await load();
}
async function removeFace(row) {
  if (await api(`/api/faces/${row}/remove`)) await load();
}
async function nameCluster(cluster, person) {
  if (!PERSON_RE.test(person) || person === "unknown") { status("Use an opaque id like p001"); return; }
  lastPerson = person;
  if (await api(`/api/clusters/${cluster.id}/name`, { person_id: person })) await load();
}
async function setComplete(photo, complete) {
  if (await api(`/api/photos/${photo.index}/complete`, { complete })) await load();
}
async function undo() {
  const res = await fetch("/api/undo", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const data = res.ok ? await res.json() : { ok: false };
  status(data.ok ? "Undone." : "Nothing to undo.");
  await load();
}

// ---- rendering ----
function faceTile(row, inCluster, isCurrent) {
  const f = state.faces[row];
  const meta = el("div", { class: "meta" }, [
    el("div", { class: "label", text: f.label ? `label: ${f.label}` : "unlabelled" }),
    el("div", { text: `#${row} ${f.photo_id}` }),
    el("div", { class: f.weak ? "weak" : "", text: `${f.size}px q=${f.quality.toFixed(2)}${f.weak ? " weak" : ""}` }),
  ]);
  const buttons = el("div", { class: "row" }, [
    el("button", { text: "label", title: "Label this face (n)", onclick: async () => {
      const p = askPerson("Person id for this face (or 'unknown'):");
      if (p) await labelFace(row, p);
    } }),
    el("button", { text: "unknown", title: "Mark as unknown person (u)", onclick: () => labelFace(row, "unknown") }),
    el("button", { text: "clear", title: "Clear label", onclick: () => labelFace(row, null) }),
  ]);
  if (inCluster) {
    buttons.append(el("button", { text: "remove", title: "Remove from this cluster (x)", onclick: () => removeFace(row) }));
  }
  return el("div", { class: isCurrent ? "face current" : "face" }, [
    el("img", { src: `/api/faces/${row}/thumb`, alt: `face ${row}`, loading: "lazy" }), meta, buttons,
  ]);
}

function cardSection(c, index) {
  const isCurrent = index === card;
  const tiles = c.faces.map((r, i) => faceTile(r, Boolean(c.cluster), isCurrent && i === face));
  const children = [];
  if (c.cluster) {
    const nameInput = el("input", { placeholder: "person id", value: c.cluster.label && c.cluster.label !== "mixed" ? c.cluster.label : "" });
    const mergeInput = el("input", { placeholder: "target cluster #", size: "6" });
    children.push(
      el("h2", { text: `Cluster #${c.cluster.id}: ${c.faces.length} face(s)` + (c.cluster.label ? `, label: ${c.cluster.label}` : "") }),
      el("div", { class: "controls" }, [
        nameInput,
        el("button", { text: "Name cluster", onclick: () => nameCluster(c.cluster, nameInput.value.trim().toLowerCase()) }),
        mergeInput,
        el("button", { text: "Merge into", onclick: async () => {
          const target = Number.parseInt(mergeInput.value, 10);
          if (!Number.isInteger(target)) { status("Enter a cluster number"); return; }
          if (await api("/api/clusters/merge", { source: c.cluster.id, target })) await load();
        } }),
      ]),
    );
  } else if (c.photo) {
    const box = el("input", { type: "checkbox", checked: c.photo.complete, onchange: (e) => setComplete(c.photo, e.target.checked) });
    children.push(
      el("h2", { text: `${c.photo.photo_id}: ${c.faces.length} face(s)` }),
      el("label", { class: "controls" }, [box, el("span", { text: " every face in this photo is labelled (c)" })]),
      el("img", { class: "preview", alt: c.photo.photo_id, loading: "lazy", src: `/api/photos/${c.photo.index}/image` }),
    );
  } else {
    children.push(el("h2", { text: `${c.faces.length} face(s) not in any cluster` }));
  }
  children.push(el("div", { class: "faces" }, tiles));
  return el("section", { class: isCurrent ? "card current" : "card", id: `card-${index}` }, children);
}

function render() {
  const main = document.getElementById("main");
  const list = cards();
  card = Math.max(0, Math.min(card, list.length - 1));
  main.replaceChildren(...(list.length ? list.map(cardSection) : [el("p", { text: "Nothing here." })]));
  const current = document.getElementById(`card-${card}`);
  if (current) current.scrollIntoView({ block: "nearest" });
}

function setView(name) {
  view = name;
  card = 0;
  face = 0;
  for (const o of document.querySelectorAll("nav button[data-view]")) o.classList.toggle("active", o.dataset.view === name);
  render();
}

// ---- keyboard shortcuts (ignored while typing in a text field) ----
document.addEventListener("keydown", async (e) => {
  if (e.target instanceof HTMLInputElement && e.target.type !== "checkbox") return;
  if (e.key === "z" && !e.altKey) { // z or Ctrl/Cmd+Z
    e.preventDefault(); await undo(); return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const list = cards();
  const c = list[card];
  const row = currentFaceRow();
  switch (e.key) {
    case "j": case "ArrowDown": card = Math.min(card + 1, list.length - 1); face = 0; render(); break;
    case "k": case "ArrowUp": card = Math.max(card - 1, 0); face = 0; render(); break;
    case "l": case "ArrowRight": if (c) { face = Math.min(face + 1, c.faces.length - 1); render(); } break;
    case "h": case "ArrowLeft": face = Math.max(face - 1, 0); render(); break;
    case "n":
      if (c && c.cluster) { const p = askPerson("Person id for the whole cluster:"); if (p && p !== "unknown") await nameCluster(c.cluster, p); }
      else if (row !== null) { const p = askPerson("Person id for this face (or 'unknown'):"); if (p) await labelFace(row, p); }
      break;
    case "x": case "Delete": if (c && c.cluster && row !== null) await removeFace(row); break;
    case "u": if (row !== null) await labelFace(row, "unknown"); break;
    case "c": if (c && c.photo) await setComplete(c.photo, !c.photo.complete); break;
    case "1": setView("clusters"); break;
    case "2": setView("unclustered"); break;
    case "3": setView("photos"); break;
    default: return;
  }
  e.preventDefault();
});

for (const b of document.querySelectorAll("nav button[data-view]")) b.addEventListener("click", () => setView(b.dataset.view));
document.getElementById("undo").addEventListener("click", undo);
load();
