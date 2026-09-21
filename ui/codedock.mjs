/* Lydia Coding Mode — lazy-loaded CodeMirror dock.
   Extracted from indexV2.html so it only loads when the dock first opens.
   Bare specifiers below resolve via the document's import map. */

/* ============================================================
   Coding Mode - step 2: live CodeMirror 6 editor
   Tabs, syntax highlighting, dirty tracking, save (FS Access
   API to disk, else /api/code/save to disk), Ctrl+S / Ctrl+W /
   Ctrl+N / Ctrl+O / Ctrl+PageUp+PageDown, drag-and-drop open.
   Every package is pinned to ONE version via the import map so
   the browser never loads duplicate copies of @codemirror/state
   (duplicates silently kill syntax highlighting).
   ============================================================ */
import { basicSetup } from 'codemirror';
import { EditorState, Compartment } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { syntaxHighlighting, HighlightStyle } from '@codemirror/language';
// NOTE: don't import closeHistory from '@codemirror/commands' — the package
// does NOT export it in any 6.x CDN build, and a missing named export is a
// hard SyntaxError at module link time (try/catch can't save it).
import { tags } from '@lezer/highlight';
import { python } from '@codemirror/lang-python';
import { javascript } from '@codemirror/lang-javascript';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';

window.CodeDock = (() => {
  const $id = (i) => document.getElementById(i);
  const tabsEl = $id('cdtabs'), editorEl = $id('cdeditor'), emptyEl = $id('cdempty');
  const statusEl = $id('cdstatus'), metaEl = $id('cdmeta'), dockEl = $id('codedock');

  // ---------- state ----------
  const files = new Map();   // id -> {id,name,path,lang,langName,content,handle,dirty}
  const tabOrder = [];       // file ids in tab order
  let activeId = null;
  let untitledN = 0;

  // ---------- CodeMirror ----------
  const langCompartment = new Compartment();
  let view = null;
  let suppressDirty = false;

  const lydiaTheme = EditorView.theme({
    '&': { color: '#eef2ff', backgroundColor: 'transparent', height: '100%' },
    '.cm-content': { caretColor: '#00f0ff', padding: '12px 0 80px', fontFamily: 'var(--mono)' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: '#00f0ff', boxShadow: '0 0 6px #00f0ff' },
    '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'rgba(90,116,208,0.40) !important' },
    '.cm-gutters': { backgroundColor: 'rgba(10,14,28,0.9)', color: '#3f4768', border: 'none', borderRight: '1px solid rgba(122,162,255,0.10)' },
    '.cm-activeLine': { backgroundColor: 'rgba(122,162,255,0.05)' },
    '.cm-activeLineGutter': { backgroundColor: 'rgba(122,162,255,0.07)', color: '#a6b0d6' },
    '.cm-lineNumbers .cm-gutterElement': { minWidth: '34px', padding: '0 8px 0 12px' },
    '.cm-foldGutter .cm-gutterElement': { padding: '0 6px' },
    '.cm-matchingBracket': { backgroundColor: 'rgba(125,227,208,0.15)', outline: '1px solid rgba(125,227,208,0.5)', color: '#7de3d0' },
    '.cm-searchMatch': { backgroundColor: 'rgba(179,136,255,0.25)', outline: '1px solid #b388ff' },
    '.cm-tooltip': { backgroundColor: '#0c101f', border: '1px solid rgba(122,162,255,0.34)', color: '#eef2ff', borderRadius: '10px', fontFamily: 'var(--mono)', boxShadow: '0 16px 40px rgba(0,0,0,0.5)' },
    '.cm-tooltip-autocomplete ul li[aria-selected]': { backgroundColor: 'rgba(90,116,208,0.35)', color: '#fff' },
    '.cm-scroller': { fontFamily: 'var(--mono)', fontSize: '12.5px', lineHeight: '1.65' }
  }, { dark: true });

  const lydiaHighlight = HighlightStyle.define([
    { tag: tags.keyword, color: '#7aa2ff', fontWeight: '600' },
    { tag: [tags.name, tags.deleted, tags.character, tags.propertyName, tags.macroName], color: '#eef2ff' },
    { tag: [tags.function(tags.variableName), tags.labelName], color: '#00f0ff' },
    { tag: [tags.color, tags.constant(tags.name), tags.standard(tags.name)], color: '#7de3d0' },
    { tag: [tags.definition(tags.name), tags.separator], color: '#eef2ff' },
    { tag: [tags.typeName, tags.className, tags.number, tags.changed, tags.annotation, tags.modifier, tags.self, tags.namespace], color: '#b388ff' },
    { tag: [tags.operator, tags.operatorKeyword, tags.escape, tags.regexp, tags.link, tags.special(tags.string)], color: '#00f0ff' },
    { tag: [tags.meta, tags.comment], color: '#6b7398', fontStyle: 'italic' },
    { tag: tags.strong, fontWeight: 'bold' },
    { tag: tags.emphasis, fontStyle: 'italic' },
    { tag: tags.strikethrough, textDecoration: 'line-through' },
    { tag: tags.heading, fontWeight: 'bold', color: '#7aa2ff' },
    { tag: [tags.atom, tags.bool, tags.special(tags.variableName)], color: '#ff6b7a' },
    { tag: [tags.processingInstruction, tags.string, tags.inserted], color: '#7de3d0' },
    { tag: tags.invalid, color: '#ff6b7a' }
  ]);

  const codeKeymap = keymap.of([
    { key: 'Mod-s', run: () => { saveActive(false); return true; } },
    { key: 'Mod-Shift-s', run: () => { saveActive(true); return true; } },
    { key: 'Mod-w', run: () => { closeTab(activeId); return true; } },
    { key: 'Mod-n', run: () => { newFile(); return true; } },
    { key: 'Mod-o', run: () => { openPicker(); return true; } },
    { key: 'Mod-PageDown', run: () => { cycleTab(1); return true; } },
    { key: 'Mod-PageUp', run: () => { cycleTab(-1); return true; } }
  ]);

  function makeState(doc, langExt) {
    return EditorState.create({
      doc: doc || '',
      extensions: [
        basicSetup,
        lydiaTheme,
        syntaxHighlighting(lydiaHighlight),
        langCompartment.of(langExt ? [langExt] : []),
        codeKeymap,
        EditorView.updateListener.of((u) => {
          if (u.docChanged) {
            if (!suppressDirty) {
              const f = files.get(activeId);
              if (f) { f.content = u.state.doc.toString(); f.dirty = true; updateTab(f); updateStatus(); }
            }
          }
          if (u.selectionSet || u.docChanged) updatePos();
        })
      ]
    });
  }

  view = new EditorView({ parent: editorEl, state: makeState('', null) });

  // ---------- language detection ----------
  function langFor(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    switch (ext) {
      case 'py': return { ext: python(), name: 'python' };
      case 'gd': return { ext: python(), name: 'gdscript~python' };
      case 'js': case 'mjs': case 'cjs': case 'ts': case 'jsx': case 'tsx': return { ext: javascript(), name: 'javascript' };
      case 'json': return { ext: json(), name: 'json' };
      case 'html': case 'htm': case 'vue': return { ext: html(), name: 'html' };
      case 'css': case 'scss': case 'less': return { ext: css(), name: 'css' };
      case 'md': case 'markdown': return { ext: markdown(), name: 'markdown' };
      default: return { ext: null, name: 'plain text' };
    }
  }

  // ---------- tabs ----------
  const tabEls = new Map();

  function uid() { return 'f' + (++untitledN) + '_' + Math.random().toString(36).slice(2, 8); }

  function addTab({ name, content, handle, path }) {
    const id = uid();
    const l = langFor(name || '');
    const f = { id, name: name || ('untitled-' + (untitledN + 1) + '.py'), content: content || '', handle: handle || null, path: path || null, dirty: false, lang: l.ext, langName: l.name };
    files.set(id, f);
    tabOrder.push(id);
    renderTabs();
    loadIntoEditor(f);
    setStatus('OPENED');
    return f;
  }

  function renderTabs() {
    tabsEl.innerHTML = '';
    tabEls.clear();
    for (const id of tabOrder) {
      const f = files.get(id);
      if (!f) continue;
      const t = document.createElement('div');
      t.className = 'cd-tab' + (id === activeId ? ' active' : '') + (f.dirty ? ' dirty' : '');
      const nm = document.createElement('span'); nm.className = 'tname'; nm.textContent = f.name;
      const dot = document.createElement('span'); dot.className = 'tdot';
      const x = document.createElement('button'); x.type = 'button'; x.className = 'cd-tabx'; x.title = 'Close'; x.textContent = '\u00d7';
      x.onclick = (e) => { e.stopPropagation(); closeTab(id); };
      t.appendChild(nm); t.appendChild(dot); t.appendChild(x);
      t.onclick = () => loadIntoEditor(files.get(id));
      tabsEl.appendChild(t);
      tabEls.set(id, t);
    }
    emptyEl.style.display = tabOrder.length ? 'none' : 'flex';
  }

  function updateTab(f) {
    const el = tabEls.get(f.id);
    if (!el) return;
    el.className = 'cd-tab' + (f.id === activeId ? ' active' : '') + (f.dirty ? ' dirty' : '');
  }

  function loadIntoEditor(f) {
    if (!f) return;
    activeId = f.id;
    suppressDirty = true;
    // History seal skipped: closeHistory isn't exported by the pinned
    // @codemirror/commands CDN builds (was previously wrapped in try/catch).
    // Worst case: Ctrl+Z right after switching files could undo the swap —
    // acceptable, and the tab content itself is never lost.
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: f.content },
      effects: langCompartment.reconfigure(f.lang ? [f.lang] : [])
    });
    view.dispatch({ selection: { anchor: 0 } });
    suppressDirty = false;
    renderTabs();
    updateStatus();
    updatePos();
    view.focus();
  }

  function closeTab(id) {
    const f = files.get(id);
    if (!f) return;
    if (f.dirty && !confirm('Close "' + f.name + '" without saving?')) return;
    files.delete(id);
    tabEls.delete(id);
    const i = tabOrder.indexOf(id);
    if (i > -1) tabOrder.splice(i, 1);
    if (activeId === id) {
      const next = tabOrder.length ? tabOrder[Math.min(i, tabOrder.length - 1)] : null;
      if (next) loadIntoEditor(files.get(next));
      else {
        activeId = null;
        suppressDirty = true;
        view.setState(makeState('', null));
        suppressDirty = false;
        renderTabs();
        setStatus('READY');
      }
    } else {
      renderTabs();
    }
  }

  function cycleTab(dir) {
    if (tabOrder.length < 2) return;
    const i = tabOrder.indexOf(activeId);
    const next = tabOrder[(i + dir + tabOrder.length) % tabOrder.length];
    loadIntoEditor(files.get(next));
  }

  // ---------- open / new ----------
  function newFile() {
    untitledN++;
    addTab({ name: 'untitled-' + untitledN + '.py', content: '' });
  }

  async function openPicker() {
    if (!window.showOpenFilePicker) {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.multiple = true;
      inp.onchange = async () => {
        for (const f of inp.files) {
          try { addTab({ name: f.name, content: await f.text() }); } catch (e) { /* noop */ }
        }
      };
      inp.click();
      return;
    }
    try {
      const handles = await window.showOpenFilePicker({ multiple: true });
      for (const h of handles) {
        try {
          const file = await h.getFile();
          addTab({ name: file.name, content: await file.text(), handle: h });
        } catch (e) { /* noop */ }
      }
    } catch (e) { /* user cancelled */ }
  }

  // ---------- save ----------
  async function saveActive(forceAs) {
    const f = files.get(activeId);
    if (!f) return;
    if (forceAs || (!f.handle && !f.path)) return saveAs(f);
    const content = view.state.doc.toString();
    try {
      if (f.handle) {
        const w = await f.handle.createWritable();
        await w.write(content);
        await w.close();
      } else if (f.path) {
        const r = await fetch('/api/code/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: f.path, content })
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
      }
      f.content = content; f.dirty = false;
      updateTab(f); setStatus('SAVED');
      if (f.path) metaEl.textContent = f.path;
    } catch (err) {
      setStatus('SAVE FAILED');
      showToastLocal('Could not save ' + f.name + ': ' + (err.message || err));
    }
  }

  async function saveAs(f) {
    if (!window.showSaveFilePicker) {
      // No FS Access API - save into imports/ via the backend.
      const name = (f.name || 'untitled.py').replace(/[^\w.\-]/g, '_');
      try {
        const r = await fetch('/api/code/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: 'imports/' + name, content: view.state.doc.toString() })
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
        f.path = j.path; f.dirty = false; f.content = view.state.doc.toString();
        updateTab(f); setStatus('SAVED TO IMPORTS');
        metaEl.textContent = f.path;
      } catch (err) {
        setStatus('SAVE FAILED');
        showToastLocal(String(err.message || err));
      }
      return;
    }
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: f.name || 'untitled.py',
        types: [{ description: 'Code', accept: { 'text/plain': ['.py', '.gd', '.js', '.ts', '.json', '.html', '.css', '.md', '.sql', '.txt', '.lua'] } }]
      });
      const w = await handle.createWritable();
      await w.write(view.state.doc.toString());
      await w.close();
      f.handle = handle; f.name = handle.name; f.path = null;
      f.dirty = false; f.content = view.state.doc.toString();
      renderTabs(); setStatus('SAVED');
    } catch (e) { /* cancelled */ }
  }

  // ---------- status ----------
  function setStatus(t) { statusEl.textContent = t; }

  function updatePos() {
    const f = files.get(activeId);
    if (!f) { metaEl.textContent = 'no file'; return; }
    const pos = view.state.selection.main.head;
    const line = view.state.doc.lineAt(pos);
    metaEl.textContent = f.langName + ' \u00b7 ' + line.number + ':' + (pos - line.from + 1) + (f.dirty ? ' \u00b7 unsaved' : '');
  }

  function updateStatus() {
    const f = files.get(activeId);
    if (!f) { metaEl.textContent = 'no file'; return; }
    // Tree-opened files carry a real path — show it so you always know what
    // disk file you're editing (and where Ctrl+S will write).
    metaEl.textContent = (f.path ? f.path : f.langName) + (f.dirty ? ' \u00b7 unsaved' : '');
  }

  // ---------- dock integration + drag & drop ----------
  function onDockToggle(open) {
    if (!open) return;
    if (view) view.requestMeasure();
    if (activeId) view.focus();
  }

  dockEl.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); });
  dockEl.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const fs = [...((e.dataTransfer && e.dataTransfer.files) || [])];
    fs.forEach((f) => f.text().then((t) => addTab({ name: f.name, content: t })).catch(() => {}));
  });

  $id('cdnew').onclick = newFile;
  $id('cdopen').onclick = openPicker;
  $id('cdsave').onclick = () => saveActive(false);
  $id('cdfolder').onclick = () => { if (window.TreeAPI && window.TreeAPI.refresh) window.TreeAPI.refresh(); };

  function showToastLocal(msg) {
    if (typeof showToast === 'function') showToast(msg, 'CODE');
  }

  // ---------- API used by the classic script (tree, dock toggle) ----------
  function openFile(o) {
    // o = {name, path, content} — path-bearing files save back to real disk
    // via POST /api/code/save (no FS Access API needed).
    return addTab({ name: o.name || 'untitled.py', content: o.content || '', path: o.path || null, handle: null });
  }
  function hasDirty() {
    for (const f of files.values()) if (f.dirty) return true;
    return false;
  }
  function countDirty() {
    let n = 0;
    for (const f of files.values()) if (f.dirty) n++;
    return n;
  }

  return { onDockToggle, openFile, hasDirty, countDirty };
})();
