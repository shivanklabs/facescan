// FaceScan labeler page. Plain JavaScript, no build step. All data is inserted as text
// (textContent / value), never as HTML.
"use strict";

const PERSON_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
let state = null;
let view = "clusters";

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
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
  render();
}

function askPerson(question) {
  const value = (window.prompt(question) || "").trim().toLowerCase();
  if (!value) return null;
  if (value !== "unknown" && !PERSON_RE.test(value)) { status("Invalid id: use a-z, 0-9, _ or -"); return null; }
  return value;
}

function faceTile(row, inCluster) {
  const f = state.faces[row];
  const meta = el("div", { class: "meta" }, [
    el("div", { class: "label", text: f.label ? `label: ${f.label}` : "unlabelled" }),
    el("div", { text: `#${row} ${f.photo_id}` }),
    el("div", { class: f.weak ? "weak" : "", text: `${f.size}px q=${f.quality.toFixed(2)}${f.weak ? " weak" : ""}` }),
  ]);
  const buttons = el("div", { class: "row" }, [
    el("button", { text: "label", title: "Label this face", onclick: async () => {
      const p = askPerson("Person id for this face (or 'unknown'):");
      if (p && await api(`/api/faces/${row}/label`, { label: p })) load();
    } }),
    el("button", { text: "unknown", title: "Mark as unknown person", onclick: async () => {
      if (await api(`/api/faces/${row}/label`, { label: "unknown" })) load();
    } }),
    el("button", { text: "clear", title: "Clear label", onclick: async () => {
      if (await api(`/api/faces/${row}/label`, { label: null })) load();
    } }),
  ]);
  if (inCluster) {
    buttons.append(el("button", { text: "remove", title: "Remove from this cluster", onclick: async () => {
      if (await api(`/api/faces/${row}/remove`)) load();
    } }));
  }
  return el("div", { class: "face" }, [
    el("img", { src: `/api/faces/${row}/thumb`, alt: `face ${row}`, loading: "lazy" }), meta, buttons,
  ]);
}

function renderClusters(main) {
  if (!state.clusters.length) { main.append(el("p", { text: "No clusters." })); return; }
  for (const c of state.clusters) {
    const nameInput = el("input", { placeholder: "person id", value: c.label && c.label !== "mixed" ? c.label : "" });
    const mergeInput = el("input", { placeholder: "target cluster #", size: "6" });
    const controls = el("div", { class: "controls" }, [
      nameInput,
      el("button", { text: "Name cluster", onclick: async () => {
        const p = nameInput.value.trim().toLowerCase();
        if (!PERSON_RE.test(p) || p === "unknown") { status("Use an opaque id like p001"); return; }
        if (await api(`/api/clusters/${c.id}/name`, { person_id: p })) load();
      } }),
      mergeInput,
      el("button", { text: "Merge into", onclick: async () => {
        const target = Number.parseInt(mergeInput.value, 10);
        if (!Number.isInteger(target)) { status("Enter a cluster number"); return; }
        if (await api("/api/clusters/merge", { source: c.id, target })) load();
      } }),
    ]);
    const title = `Cluster #${c.id}: ${c.faces.length} face(s)` + (c.label ? `, label: ${c.label}` : "");
    main.append(el("section", { class: "card" }, [
      el("h2", { text: title }), controls,
      el("div", { class: "faces" }, c.faces.map((r) => faceTile(r, true))),
    ]));
  }
}

function renderUnclustered(main) {
  main.append(el("section", { class: "card" }, [
    el("h2", { text: `${state.unclustered.length} face(s) not in any cluster` }),
    el("div", { class: "faces" }, state.unclustered.map((r) => faceTile(r, false))),
  ]));
}

function renderPhotos(main) {
  for (const p of state.photos) {
    const img = el("img", { class: "preview", alt: p.photo_id, loading: "lazy", src: `/api/photos/${p.index}/image` });
    main.append(el("section", { class: "card" }, [
      el("h2", { text: `${p.photo_id}: ${p.faces.length} face(s)` }), img,
      el("div", { class: "faces" }, p.faces.map((r) => faceTile(r, false))),
    ]));
  }
}

function render() {
  const main = document.getElementById("main");
  main.replaceChildren();
  if (view === "clusters") renderClusters(main);
  else if (view === "unclustered") renderUnclustered(main);
  else renderPhotos(main);
}

for (const b of document.querySelectorAll("nav button")) {
  b.addEventListener("click", () => {
    view = b.dataset.view;
    for (const o of document.querySelectorAll("nav button")) o.classList.toggle("active", o === b);
    render();
  });
}
load();
