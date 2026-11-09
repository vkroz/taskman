"""WSGI application: dashboard, task API, liveness, and readiness.

The browser polls ``/api/dashboard``. The server does not open connections to agents.
``GET /health`` does not touch Postgres. ``GET /ready`` runs ``SELECT 1``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from taskman.access import configured_access_token, secrets_equal
from taskman.db import ping
from taskman.store import (
    TaskmanError,
    complete_task,
    context,
    create_task,
    dashboard,
    get_task,
    list_tasks,
    pull_task,
    ready_tasks,
    set_dependencies,
    update_task,
    add_event,
    with_connection,
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Taskman · Flight control</title>
  <style>
    :root {
      color: #202738;
      background: #f7f8fb;
      font: 14px/1.45 Inter, -apple-system, system-ui, sans-serif;
      --line: #e4e7ef;
      --muted: #667085;
      --accent: #5145cd;
      --sidebar-width: 220px;
      --detail-width: 380px;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body { margin: 0; overflow: hidden; }
    button, input { font: inherit; }
    button {
      cursor: pointer;
      border: 1px solid var(--line);
      background: white;
      border-radius: 7px;
      padding: 8px 12px;
      color: inherit;
    }
    button:hover { background: #f0efff; border-color: #c7c1ef; }
    button:focus-visible, input:focus-visible { outline: 3px solid #aea6ff; outline-offset: 2px; }
    button.primary { background: var(--accent); color: white; border-color: var(--accent); }
    button.primary:hover { background: #4035b0; }
    h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.4px; }
    p { margin: 0; }
    .shell { display: flex; height: 100%; min-width: 0; min-height: 0; }
    .sidebar {
      width: var(--sidebar-width);
      flex: none;
      background: #f0f2f7;
      padding: 22px 14px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      overflow: auto;
      min-height: 0;
    }
    .brand { display: flex; align-items: center; gap: 8px; font-weight: 750; font-size: 20px; letter-spacing: -0.5px; }
    .mark {
      width: 28px; height: 28px; border-radius: 8px; background: var(--accent); color: white;
      display: inline-flex; align-items: center; justify-content: center; font-size: 16px;
    }
    .eyebrow {
      margin: 0 8px 8px; font-size: 11px; font-weight: 700; letter-spacing: 1.1px;
      text-transform: uppercase; color: var(--muted);
    }
    .nav { display: grid; gap: 4px; }
    .nav button { text-align: left; border: 0; background: transparent; padding: 9px 12px; }
    .nav button[aria-current="true"] { background: #e3dff8; color: #4233ac; font-weight: 650; }
    .sidebar footer { margin-top: auto; color: var(--muted); font-size: 12px; line-height: 1.5; padding: 8px; }
    .list-pane {
      flex: 1 1 auto; min-width: 0; min-height: 0; display: flex; flex-direction: column; background: #f7f8fb;
    }
    .list-head {
      display: flex; flex-wrap: wrap; gap: 16px; align-items: end; justify-content: space-between;
      padding: 20px 20px 12px;
    }
    .subtitle, #state { color: var(--muted); }
    #state { min-height: 1.2em; margin-top: 4px; }
    .token { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }
    label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 600; }
    input { padding: 8px; min-width: 14rem; border: 1px solid #d4d9e3; border-radius: 7px; background: white; color: inherit; }
    .table-wrap { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 0 16px 16px; }
    #count { color: var(--muted); font-size: 12px; margin: 0 4px 8px; }
    .surface { background: white; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
    table { width: 100%; border-collapse: collapse; min-width: 720px; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { font-size: 12px; color: var(--muted); position: sticky; top: 0; background: white; }
    tbody tr { cursor: pointer; }
    tbody tr:hover { background: #f7f6ff; }
    tbody tr.selected { background: #efebff; }
    tr.done { color: var(--muted); }
    td.empty { text-align: center; color: var(--muted); padding: 36px 16px; cursor: default; }
    .title-button { border: 0; background: transparent; padding: 0; font-weight: 650; text-align: left; }
    .title-button:hover { color: var(--accent); background: transparent; }
    .detail {
      width: var(--detail-width); flex: none; background: white; overflow: auto; padding: 22px; min-height: 0;
    }
    .detail[hidden] { display: none !important; }
    .detail-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; color: var(--muted); font-size: 12px; }
    .detail h2 { margin: 12px 0; font-size: 20px; line-height: 1.35; overflow-wrap: anywhere; }
    .detail h3 {
      margin: 22px 0 8px; font-size: 12px; letter-spacing: 0.6px; text-transform: uppercase; color: var(--muted);
    }
    .body-text, .event { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; }
    .event { border-left: 2px solid #dcd6fb; padding: 0 0 0 12px; margin: 12px 0; }
    .event-head { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .muted { color: var(--muted); }
    .detail p.muted { margin-top: 10px; }
    .badge {
      display: inline-block; border-radius: 99px; padding: 2px 8px; font-size: 12px;
      text-transform: capitalize; background: #f0f2f6;
    }
    .badge.active { color: #5841b0; background: #efebff; }
    .badge.done { color: #18724c; background: #e9f6ef; }
    .icon-button { padding: 4px 8px; line-height: 1; }
    .splitter { position: relative; flex: 0 0 0; align-self: stretch; z-index: 5; }
    .splitter[hidden] { display: none !important; }
    .splitter-grip {
      position: absolute; top: 0; bottom: 0; left: -7px; width: 14px; cursor: col-resize;
      touch-action: none; user-select: none;
    }
    .splitter-grip::before {
      content: ""; position: absolute; top: 0; bottom: 0; left: 6px; width: 1px; background: var(--line);
    }
    .splitter-grip::after {
      content: ""; position: absolute; top: 50%; left: 7px; width: 4px; height: 28px;
      transform: translate(-50%, -50%); border-radius: 99px; background: var(--accent); opacity: 0;
    }
    .splitter:hover .splitter-grip,
    .splitter:focus-visible .splitter-grip,
    .splitter.is-dragging .splitter-grip { background: rgba(81, 69, 205, 0.1); }
    .splitter:hover .splitter-grip::before,
    .splitter:focus-visible .splitter-grip::before,
    .splitter.is-dragging .splitter-grip::before { left: 5px; width: 3px; background: var(--accent); }
    .splitter:hover .splitter-grip::after,
    .splitter:focus-visible .splitter-grip::after,
    .splitter.is-dragging .splitter-grip::after { opacity: 1; }
    .splitter:focus { outline: none; }
    .splitter:focus-visible .splitter-grip { outline: 3px solid #aea6ff; outline-offset: -3px; }
    body.is-resizing, body.is-resizing * { cursor: col-resize !important; user-select: none !important; }
    body.is-resizing iframe, body.is-resizing object, body.is-resizing embed { pointer-events: none !important; }
    #resize-shield { position: fixed; inset: 0; z-index: 80; cursor: col-resize; }
    #resize-shield[hidden] { display: none !important; }
    @media (max-width: 899px) {
      body { overflow: auto; }
      .shell { flex-direction: column; height: auto; min-height: 100%; }
      .sidebar, .detail { width: auto; }
      .sidebar { gap: 10px; padding: 14px; }
      .nav { display: flex; overflow: auto; }
      .sidebar footer { display: none; }
      .splitter { display: none; }
      .list-pane { min-height: 60vh; }
      .detail { border-top: 1px solid var(--line); }
    }
  </style>
</head>
<body>
  <div class="shell" id="shell">
    <aside class="sidebar" id="pane-sidebar">
      <div class="brand"><span class="mark">t</span> Taskman</div>
      <div>
        <p class="eyebrow">Workspace</p>
        <nav class="nav" aria-label="Work views">
          <button type="button" data-view="all" aria-current="true">All work</button>
          <button type="button" data-view="current" aria-current="false">Current</button>
          <button type="button" data-view="backlog" aria-current="false">Backlog</button>
          <button type="button" data-view="done" aria-current="false">Done</button>
        </nav>
      </div>
      <footer>Drag a border to resize. Double-click it to reset. Arrow keys change a focused border.</footer>
    </aside>
    <div
      class="splitter"
      id="split-sidebar"
      data-pane="sidebar"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-controls="pane-sidebar"
      aria-valuemin="176"
      aria-valuemax="420"
      aria-valuenow="220"
      aria-valuetext="220 pixels"
      tabindex="0"
      title="Drag to resize. Double-click to reset."
    ><span class="splitter-grip" aria-hidden="true"></span></div>
    <main class="list-pane" id="pane-list">
      <header class="list-head">
        <div>
          <h1 id="view-title">All work</h1>
          <p class="subtitle">Flight control. Plan, follow through, keep the context.</p>
          <p id="state" role="status">Enter the access token.</p>
        </div>
        <div class="token">
          <label>Access token
            <input id="token" type="password" autocomplete="current-password">
          </label>
          <button class="primary" id="save" type="button">Use token</button>
          <button id="clear" type="button">Clear</button>
        </div>
      </header>
      <div class="table-wrap">
        <p id="count">0 work items</p>
        <div class="surface">
          <table aria-label="Work items">
            <thead>
              <tr>
                <th>Task</th><th>Title</th><th>Status</th><th>Agent</th><th>Session</th>
                <th>Last event</th><th>When</th><th>Detail</th>
              </tr>
            </thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
      </div>
    </main>
    <div
      class="splitter"
      id="split-detail"
      data-pane="detail"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize details panel"
      aria-controls="pane-detail"
      aria-valuemin="280"
      aria-valuemax="640"
      aria-valuenow="380"
      aria-valuetext="380 pixels"
      tabindex="0"
      title="Drag to resize. Double-click to reset."
      hidden
    ><span class="splitter-grip" aria-hidden="true"></span></div>
    <aside class="detail" id="pane-detail" aria-label="Task details" hidden></aside>
  </div>
  <div id="resize-shield" hidden></div>
  <script>
    const STORAGE_KEY = 'taskman.pane-widths';
    const DEFAULTS = { sidebar: 220, detail: 380 };
    const LIMITS = { sidebar: { min: 176, max: 420 }, detail: { min: 280, max: 640 } };
    const LIST_MIN = 360;
    const STACKED = window.matchMedia('(max-width: 899px)');
    const VIEW_TITLES = { all: 'All work', current: 'Current', backlog: 'Backlog', done: 'Done' };
    const state = document.getElementById('state');
    const rowsEl = document.getElementById('rows');
    const tokenInput = document.getElementById('token');
    const countEl = document.getElementById('count');
    const sidebarPane = document.getElementById('pane-sidebar');
    const detailPane = document.getElementById('pane-detail');
    const detailSplit = document.getElementById('split-detail');
    const shield = document.getElementById('resize-shield');
    const rootStyle = document.documentElement.style;
    let view = 'all';
    let rows = [];
    let selectedId = null;
    let detailSerial = 0;
    let detailSignature = '';
    let drag = null;

    function clamp(value, min, max) {
      const upper = Math.max(min, max);
      return Math.round(Math.min(upper, Math.max(min, value)));
    }
    function stacked() { return STACKED.matches; }
    function readStored() {
      const next = { sidebar: DEFAULTS.sidebar, detail: DEFAULTS.detail };
      try {
        const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
        if (!data || typeof data !== 'object') return next;
        Object.keys(LIMITS).forEach((kind) => {
          const value = Number(data[kind]);
          if (Number.isFinite(value)) next[kind] = clamp(value, LIMITS[kind].min, LIMITS[kind].max);
        });
      } catch (error) { /* keep defaults when storage is blocked */ }
      return next;
    }
    let preferred = readStored();
    function detailOpen() { return !detailPane.hidden; }
    function measure() {
      const viewport = document.documentElement.clientWidth;
      let sidebar = clamp(preferred.sidebar, LIMITS.sidebar.min, LIMITS.sidebar.max);
      let detail = clamp(preferred.detail, LIMITS.detail.min, LIMITS.detail.max);
      if (!stacked()) {
        const reserve = detailOpen() ? LIMITS.detail.min : 0;
        const sidebarMax = Math.min(LIMITS.sidebar.max, viewport - LIST_MIN - reserve);
        sidebar = clamp(sidebar, LIMITS.sidebar.min, Math.max(LIMITS.sidebar.min, sidebarMax));
        if (detailOpen()) {
          const detailMax = Math.min(LIMITS.detail.max, viewport - sidebar - LIST_MIN);
          detail = clamp(detail, LIMITS.detail.min, Math.max(LIMITS.detail.min, detailMax));
        }
      }
      return { sidebar: sidebar, detail: detail };
    }
    function paintableRange(kind) {
      if (stacked()) return { min: LIMITS[kind].min, max: LIMITS[kind].max };
      const viewport = document.documentElement.clientWidth;
      if (kind === 'sidebar') {
        const reserve = detailOpen() ? LIMITS.detail.min : 0;
        const max = Math.min(LIMITS.sidebar.max, viewport - LIST_MIN - reserve);
        return { min: LIMITS.sidebar.min, max: Math.max(LIMITS.sidebar.min, max) };
      }
      const sidebar = measure().sidebar;
      const max = Math.min(LIMITS.detail.max, viewport - sidebar - LIST_MIN);
      return { min: LIMITS.detail.min, max: Math.max(LIMITS.detail.min, max) };
    }
    function setAria(splitter, now, range) {
      splitter.setAttribute('aria-valuemin', String(range.min));
      splitter.setAttribute('aria-valuemax', String(range.max));
      splitter.setAttribute('aria-valuenow', String(now));
      splitter.setAttribute('aria-valuetext', now + ' pixels');
    }
    function paint() {
      const sizes = measure();
      rootStyle.setProperty('--sidebar-width', sizes.sidebar + 'px');
      rootStyle.setProperty('--detail-width', sizes.detail + 'px');
      setAria(document.getElementById('split-sidebar'), sizes.sidebar, paintableRange('sidebar'));
      setAria(detailSplit, sizes.detail, paintableRange('detail'));
    }
    function persist() {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferred)); } catch (error) { /* ignore */ }
    }
    function finishDrag() {
      if (!drag) return;
      drag.splitter.classList.remove('is-dragging');
      document.body.classList.remove('is-resizing');
      shield.hidden = true;
      window.removeEventListener('pointermove', moveDrag, true);
      window.removeEventListener('pointerup', finishDrag, true);
      window.removeEventListener('pointercancel', finishDrag, true);
      window.removeEventListener('blur', finishDrag);
      drag = null;
      persist();
    }
    function moveDrag(event) {
      if (!drag) return;
      const pane = drag.kind === 'sidebar' ? sidebarPane : detailPane;
      const rect = pane.getBoundingClientRect();
      const pointed = drag.kind === 'sidebar' ? event.clientX - rect.left : rect.right - event.clientX;
      const range = paintableRange(drag.kind);
      preferred[drag.kind] = clamp(pointed - drag.offset, range.min, range.max);
      paint();
    }
    function bindSplitter(splitter) {
      const kind = splitter.dataset.pane;
      splitter.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || stacked() || drag) return;
        event.preventDefault();
        splitter.focus({ preventScroll: true });
        const pane = kind === 'sidebar' ? sidebarPane : detailPane;
        const rect = pane.getBoundingClientRect();
        const pointed = kind === 'sidebar' ? event.clientX - rect.left : rect.right - event.clientX;
        drag = { kind: kind, splitter: splitter, offset: pointed - measure()[kind] };
        splitter.classList.add('is-dragging');
        document.body.classList.add('is-resizing');
        shield.hidden = false;
        try { splitter.setPointerCapture(event.pointerId); } catch (error) { /* overlay still catches the pointer */ }
        window.addEventListener('pointermove', moveDrag, true);
        window.addEventListener('pointerup', finishDrag, true);
        window.addEventListener('pointercancel', finishDrag, true);
        window.addEventListener('blur', finishDrag);
      });
      splitter.addEventListener('dblclick', (event) => {
        if (stacked()) return;
        event.preventDefault();
        const selection = window.getSelection();
        if (selection) selection.removeAllRanges();
        preferred[kind] = DEFAULTS[kind];
        paint();
        persist();
      });
      splitter.addEventListener('keydown', (event) => {
        if (stacked()) return;
        const range = paintableRange(kind);
        if (event.key === 'Enter') {
          event.preventDefault();
          preferred[kind] = DEFAULTS[kind];
          paint();
          persist();
          return;
        }
        if (event.key === 'Home' || event.key === 'End') {
          event.preventDefault();
          preferred[kind] = event.key === 'Home' ? range.min : range.max;
          paint();
          persist();
          return;
        }
        let direction = 0;
        if (event.key === 'ArrowRight' || event.key === 'ArrowUp') direction = 1;
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') direction = -1;
        else return;
        event.preventDefault();
        const step = event.shiftKey ? 64 : 16;
        const sign = kind === 'sidebar' ? 1 : -1;
        const current = measure()[kind];
        preferred[kind] = clamp(current + direction * sign * step, range.min, range.max);
        paint();
        persist();
      });
    }
    document.querySelectorAll('.splitter').forEach(bindSplitter);
    paint();
    window.addEventListener('resize', paint);
    STACKED.addEventListener('change', paint);
    document.addEventListener('selectstart', (event) => {
      if (document.body.classList.contains('is-resizing')) event.preventDefault();
    });

    function token() { return sessionStorage.getItem('taskman_token') || ''; }
    function el(tag, attrs, text) {
      const node = document.createElement(tag);
      if (attrs) Object.keys(attrs).forEach((key) => node.setAttribute(key, attrs[key]));
      if (text != null) node.textContent = text;
      return node;
    }
    function cell(text) {
      const td = document.createElement('td');
      td.textContent = text || '';
      return td;
    }
    function when(value) {
      if (!value) return '';
      return String(value).replace('T', ' ').slice(0, 16);
    }
    function matchesView(row) {
      if (view === 'current') return row.status === 'active' || row.status === 'backlog';
      if (view === 'backlog' || view === 'done') return row.status === view;
      return true;
    }
    function highlight() {
      rowsEl.querySelectorAll('tr').forEach((tr) => {
        tr.classList.toggle('selected', tr.dataset.id === String(selectedId || ''));
      });
    }
    function renderRows() {
      const visible = rows.filter(matchesView);
      const next = document.createDocumentFragment();
      if (!visible.length) {
        const tr = document.createElement('tr');
        const message = rows.length ? 'No tasks in this view.' : (token() ? 'No tasks yet.' : 'Enter the access token to load work.');
        tr.appendChild(el('td', { colspan: '8', class: 'empty' }, message));
        next.appendChild(tr);
      } else {
        visible.forEach((row) => {
          const tr = document.createElement('tr');
          tr.dataset.id = String(row.id);
          if (row.status === 'done') tr.className = 'done';
          if (String(row.id) === String(selectedId)) tr.classList.add('selected');
          tr.appendChild(cell(String(row.id)));
          const title = document.createElement('td');
          const button = el('button', { type: 'button', class: 'title-button' }, row.title || '');
          title.appendChild(button);
          tr.appendChild(title);
          [row.status, row.agent, row.session, row.last_event, when(row.last_activity), row.detail]
            .forEach((value) => tr.appendChild(cell(value)));
          tr.addEventListener('click', () => openDetail(row.id));
          next.appendChild(tr);
        });
      }
      rowsEl.replaceChildren(next);
      countEl.textContent = visible.length === 1 ? '1 work item' : visible.length + ' work items';
    }
    function renderDetailMessage(text) {
      detailPane.replaceChildren(el('p', null, text));
    }
    function renderDetail(payload) {
      const task = payload.task || {};
      const scroll = detailPane.scrollTop;
      const fragment = document.createDocumentFragment();
      const top = el('div', { class: 'detail-top' });
      top.appendChild(el('span', null, (task.type || 'task') + ' · ' + (task.id != null ? task.id : '')));
      const close = el('button', { type: 'button', class: 'icon-button', id: 'close-details', 'aria-label': 'Close task details' }, '×');
      close.addEventListener('click', closeDetail);
      top.appendChild(close);
      fragment.appendChild(top);
      fragment.appendChild(el('h2', null, task.title || ''));
      if (task.status === 'backlog' || task.status === 'active' || task.status === 'done') {
        fragment.appendChild(el('span', { class: 'badge ' + task.status }, task.status));
      }
      if (payload.parent) {
        fragment.appendChild(el('p', { class: 'muted' }, 'Parent: ' + (payload.parent.title || payload.parent.id)));
      }
      fragment.appendChild(el('h3', null, 'Description'));
      fragment.appendChild(el('div', { class: 'body-text' }, task.body ? task.body : 'No description yet.'));
      fragment.appendChild(el('h3', null, 'Events'));
      const events = payload.events || [];
      if (!events.length) {
        fragment.appendChild(el('p', { class: 'muted' }, 'No events yet.'));
      } else {
        events.slice().reverse().forEach((item) => {
          const block = el('div', { class: 'event' });
          const head = [item.kind, item.agent, when(item.created_at)].filter(Boolean).join(' · ');
          block.appendChild(el('div', { class: 'event-head' }, head));
          block.appendChild(el('div', null, item.message || item.detail || ''));
          fragment.appendChild(block);
        });
      }
      detailPane.replaceChildren(fragment);
      detailPane.scrollTop = scroll;
    }
    function closeDetail() {
      const current = selectedId;
      selectedId = null;
      detailSerial += 1;
      detailSignature = '';
      detailPane.hidden = true;
      detailSplit.hidden = true;
      detailPane.replaceChildren();
      paint();
      highlight();
      const button = document.querySelector('tr[data-id="' + current + '"] .title-button');
      if (button) button.focus();
    }
    function openDetail(id) {
      const text = String(id);
      if (!/^[0-9]+$/.test(text)) return;
      const changed = String(selectedId) !== text;
      selectedId = text;
      detailPane.hidden = false;
      detailSplit.hidden = false;
      paint();
      highlight();
      if (changed) {
        detailSignature = '';
        detailPane.scrollTop = 0;
        renderDetailMessage('Loading…');
      }
      loadDetail(text);
    }
    async function loadDetail(id) {
      const serial = ++detailSerial;
      try {
        const response = await fetch('/api/tasks/' + id + '/context', { headers: { Authorization: 'Bearer ' + token() } });
        if (serial !== detailSerial) return;
        if (response.status === 401) {
          state.textContent = 'Token was rejected.';
          closeDetail();
          return;
        }
        if (!response.ok) {
          renderDetailMessage('This task is no longer available.');
          return;
        }
        const payload = await response.json();
        if (serial !== detailSerial) return;
        const signature = JSON.stringify(payload);
        if (signature === detailSignature) return;
        detailSignature = signature;
        renderDetail(payload);
      } catch (error) {
        if (serial !== detailSerial) return;
        renderDetailMessage(error.message);
      }
    }
    async function refresh() {
      const value = token();
      if (!value) {
        state.textContent = 'Enter the access token. It stays in this browser tab.';
        rows = [];
        renderRows();
        if (selectedId) closeDetail();
        return;
      }
      try {
        const response = await fetch('/api/dashboard', { headers: { Authorization: 'Bearer ' + value } });
        if (response.status === 401) {
          state.textContent = 'Token was rejected.';
          rows = [];
          renderRows();
          if (selectedId) closeDetail();
          return;
        }
        if (!response.ok) throw new Error('Dashboard request failed.');
        const data = await response.json();
        rows = data.rows || [];
        const stillThere = selectedId && rows.some((row) => String(row.id) === String(selectedId));
        if (selectedId && !stillThere) closeDetail();
        renderRows();
        state.textContent = rows.length ? 'Updated from reported agent events.' : 'No tasks yet.';
        if (stillThere) loadDetail(selectedId);
      } catch (error) {
        state.textContent = error.message;
      }
    }
    function saveToken() {
      sessionStorage.setItem('taskman_token', tokenInput.value.trim());
      tokenInput.value = '';
      refresh();
    }
    document.getElementById('save').addEventListener('click', saveToken);
    tokenInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); saveToken(); }
    });
    document.getElementById('clear').addEventListener('click', () => {
      sessionStorage.removeItem('taskman_token');
      refresh();
    });
    document.querySelectorAll('[data-view]').forEach((button) => {
      button.addEventListener('click', () => {
        view = button.dataset.view;
        document.getElementById('view-title').textContent = VIEW_TITLES[view] || 'All work';
        document.querySelectorAll('[data-view]').forEach((item) => {
          item.setAttribute('aria-current', item === button ? 'true' : 'false');
        });
        renderRows();
      });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !detailPane.hidden && event.target !== tokenInput) closeDetail();
    });
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def _inline_hash(tag: str) -> str:
    """CSP source for the inline <script> or <style> block in ``_PAGE``."""
    content = _PAGE.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# The page may run only its own inline script and style, may call only this
# origin, and may not be framed. Keep a single <style> and a single <script>:
# each hash covers the first block of that tag. JSON responses allow nothing.
_PAGE_CSP = (
    "default-src 'none'; "
    f"script-src {_inline_hash('script')}; "
    f"style-src {_inline_hash('style')}; "
    "connect-src 'self'; img-src 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
_API_CSP = "default-src 'none'; frame-ancestors 'none'"

# Largest value of a Postgres BIGINT, the type of every id column.
_MAX_ID = 2**63 - 1
_ID_TEXT = re.compile(r"[0-9]{1,19}")


def _status_line(status: int) -> str:
    phrases = {
        200: "OK",
        201: "Created",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        413: "Payload Too Large",
        405: "Method Not Allowed",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    return f"{status} {phrases.get(status, 'Error')}"


def _send(
    start_response,
    status: int,
    body: bytes,
    content_type: str,
    extra: list[tuple[str, str]] | None = None,
    csp: str = _API_CSP,
):
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Content-Security-Policy", csp),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
    ]
    if extra:
        headers.extend(extra)
    start_response(_status_line(status), headers)
    return [body]


def _json(start_response, status: int, payload, extra=None):
    return _send(start_response, status, json.dumps(payload).encode("utf-8"), "application/json", extra)


def _authorized(environ: dict, token: str) -> bool:
    header = environ.get("HTTP_AUTHORIZATION", "")
    scheme, separator, provided = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return False
    return secrets_equal(provided, token)


def _read_json(environ: dict, limit: int = 1_000_000) -> dict:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError as exc:
        raise TaskmanError(400, "Invalid request size.") from exc
    if length < 0:
        raise TaskmanError(400, "Invalid request size.")
    if length > limit:
        raise TaskmanError(413, "Request body is too large.")
    raw = environ["wsgi.input"].read(length) if length else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise TaskmanError(400, "Expected JSON.") from exc
    if not isinstance(data, dict):
        raise TaskmanError(400, "Expected an object.")
    return data


def _task_id(path: str, prefix: str) -> tuple[int, str]:
    rest = path[len(prefix) :]
    ident, _, tail = rest.partition("/")
    # ASCII digits only: str.isdigit() also accepts characters such as "²".
    if not _ID_TEXT.fullmatch(ident) or not 1 <= int(ident) <= _MAX_ID:
        raise TaskmanError(404, "task not found")
    return int(ident), tail


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_ID


def _optional_int(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    if not _is_id(value):
        raise TaskmanError(400, f"{name} must be a task id.")
    return value


def _text(data: dict, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise TaskmanError(400, f"{key} must be text.")
    return value


def _route(method: str, path: str, data: dict):
    if method == "POST" and path == "/api/tasks":
        parent = _optional_int(data.get("parent_id"), "parent_id")
        task = with_connection(
            create_task,
            _text(data, "title"),
            _text(data, "body"),
            _text(data, "type", "task") or "task",
            parent,
        )
        return 201, task
    if path == "/api/dashboard" and method == "GET":
        return 200, {"rows": with_connection(dashboard)}
    if path == "/api/ready" and method == "GET":
        return 200, {"tasks": with_connection(ready_tasks)}
    if not path.startswith("/api/tasks/"):
        raise TaskmanError(404, "Unknown endpoint.")
    task_id, tail = _task_id(path, "/api/tasks/")
    if tail == "" and method == "GET":
        return 200, with_connection(get_task, task_id)
    if tail == "" and method == "PATCH":
        fields = {}
        if "title" in data:
            fields["title"] = _text(data, "title")
        if "body" in data:
            fields["body"] = _text(data, "body")
        if "status" in data:
            fields["status"] = _text(data, "status")
        if not fields:
            raise TaskmanError(400, "Nothing to update.")
        return 200, with_connection(update_task, task_id, **fields)
    if tail == "dependencies" and method == "PUT":
        raw = data.get("depends_on", [])
        if not isinstance(raw, list) or not all(_is_id(item) for item in raw):
            raise TaskmanError(400, "depends_on must be a list of task ids.")
        ids = with_connection(set_dependencies, task_id, raw)
        return 200, {"task_id": task_id, "depends_on": ids}
    if tail == "context" and method == "GET":
        return 200, with_connection(context, task_id)
    if tail == "events" and method == "POST":
        event, created = with_connection(
            add_event,
            task_id,
            _text(data, "kind"),
            agent=_text(data, "agent"),
            session=_text(data, "session"),
            commit_sha=_text(data, "commit_sha"),
            message=_text(data, "message"),
            pr_url=_text(data, "pr_url"),
        )
        # 201 only when a row was inserted. A duplicate commit sha, or a
        # completion that repeats the latest one for the same agent and
        # session, returns 200 and the stored event. 409 would turn a
        # successful retry into an error for the CLI and the hooks.
        return (201 if created else 200), event
    if tail == "pull" and method == "POST":
        return 200, with_connection(pull_task, task_id, _text(data, "agent"), _text(data, "session"))
    if tail == "complete" and method == "POST":
        return 200, with_connection(
            complete_task, task_id, _text(data, "message"), _text(data, "agent"), _text(data, "session")
        )
    raise TaskmanError(404, "Unknown endpoint.")


def dispatch(environ, start_response, token):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query_status = None
    if path == "/api/tasks" and method == "GET":
        from urllib.parse import parse_qs

        query_status = parse_qs(environ.get("QUERY_STRING", "")).get("status", [None])[0]
    try:
        if method == "GET" and path == "/health":
            return _json(start_response, 200, {"status": "ok"})
        if method == "GET" and path == "/ready":
            try:
                ping()
            except Exception as exc:  # noqa: BLE001 — any failure means not ready
                print(f"readiness check failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                return _json(start_response, 503, {"status": "unavailable"})
            return _json(start_response, 200, {"status": "ok"})
        if method == "GET" and path == "/":
            return _send(start_response, 200, _PAGE.encode("utf-8"), "text/html; charset=utf-8", csp=_PAGE_CSP)
        if token is None:
            return _json(start_response, 503, {"error": "TASKMAN_ACCESS_TOKEN is not configured."})
        if not _authorized(environ, token):
            return _json(
                start_response,
                401,
                {"error": "Authentication required."},
                [("WWW-Authenticate", "Bearer")],
            )
        if method == "GET" and path == "/api/tasks":
            status, payload = 200, {"tasks": with_connection(list_tasks, query_status)}
        elif method in {"POST", "PUT", "PATCH"}:
            status, payload = _route(method, path, _read_json(environ))
        else:
            status, payload = _route(method, path, {})
        return _json(start_response, status, payload)
    except TaskmanError as exc:
        return _json(start_response, exc.status, {"error": exc.message})
    except Exception:
        import traceback

        traceback.print_exc()
        return _json(start_response, 500, {"error": "Internal error"})


def application(environ, start_response):
    """Production entry point. Reads the token from the environment per request."""
    return dispatch(environ, start_response, configured_access_token())


def make_application(token: str):
    """WSGI app with a fixed token. Tests use this so the client can change os.environ."""

    def _app(environ, start_response):
        return dispatch(environ, start_response, token)

    return _app
