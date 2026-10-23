import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const source = fs.readFileSync(new URL('../naia_history_library.js', import.meta.url), 'utf8');
const pure = {module: {exports: {}}};
vm.runInNewContext(source, pure);
const {jobSummary, metadataText, selectForComparison, createTaskQueue, isHistoryOpener} = pure.module.exports;
const flush = async () => { for (let i = 0; i < 3; i++) await new Promise(resolve => setImmediate(resolve)); };

// A focused DOM double exercises real user flows without a phone, native picker,
// network connection, credentials or existing generated history.
function fixture(options = {}) {
  const observers = [], calls = [], assets = [], posts = [], urls = new Set(), revoked = [], timers = new Map();
  let document, timerId = 0, urlId = 0;
  class Element {
    constructor(tag) {
      this.nodeType = 1; this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null;
      this.attributes = {}; this.events = new Map(); this._text = ''; this.className = ''; this.disabled = false;
      this.hidden = false; this.checked = false; this.open = false; this.onclick = null; this.style = {};
      this.classList = {
        contains: name => this.className.split(' ').includes(name),
        add: name => { if (!this.classList.contains(name)) this.className = (this.className + ' ' + name).trim(); },
        remove: name => { this.className = this.className.split(' ').filter(value => value !== name).join(' '); },
        toggle: (name, enabled) => { const on = enabled == null ? !this.classList.contains(name) : enabled; on ? this.classList.add(name) : this.classList.remove(name); return on; },
      };
    }
    get isConnected() { for (let node = this; node; node = node.parentElement) if (node === document) return true; return false; }
    get childElementCount() { return this.children.length; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._text = String(value ?? ''); for (const child of this.children) child.parentElement = null; this.children = []; }
    append(...values) { for (const value of values) { value.remove(); value.parentElement = this; this.children.push(value); } }
    replaceChildren(...values) { this.textContent = ''; this.append(...values); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; }
    setAttribute(key, value) { this.attributes[key] = String(value); if (key === 'class') this.className = String(value); if (key === 'open') this.open = true; }
    getAttribute(key) { return key in this.attributes ? this.attributes[key] : null; }
    hasAttribute(key) { return key in this.attributes; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(type, action, capture) { if (!this.events.has(type)) this.events.set(type, []); this.events.get(type).push({action, capture: capture === true}); }
    removeEventListener(type, action) { this.events.set(type, (this.events.get(type) || []).filter(value => value.action !== action)); }
    matches(selector) {
      return selector.split(',').some(part => {
        part = part.trim(); const tag = part.match(/^[a-z]+/i); if (tag && this.tagName !== tag[0].toUpperCase()) return false;
        for (const match of part.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
        for (const match of part.matchAll(/\[([^\]*=]+)(\*=|=)?(?:"([^"]*)")?\]/g)) {
          const value = this.getAttribute(match[1]); if (value == null) return false;
          if (match[2] === '=' && value !== match[3]) return false;
          if (match[2] === '*=' && !value.includes(match[3])) return false;
        }
        return true;
      });
    }
    querySelectorAll(selector) { const result = []; for (const child of this.children) { if (child.matches(selector)) result.push(child); result.push(...child.querySelectorAll(selector)); } return result; }
    closest(selector) { for (let value = this; value && value !== document; value = value.parentElement) if (value.matches(selector)) return value; return null; }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; this.focus(); }
    close() { this.open = false; }
    dispatch(type) {
      const event = {target: this, stopped: false, preventDefault() {}, stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.stopped = true; }};
      for (const entry of document.events.get(type) || []) if (entry.capture) entry.action(event);
      if (!event.stopped) for (const entry of this.events.get(type) || []) entry.action(event);
      return event;
    }
    click() { if (this.disabled) return; if (this.type === 'checkbox') { this.checked = !this.checked; this.dispatch('change'); } else this.dispatch('click'); }
  }
  document = new Element('document'); document.nodeType = 9;
  document.body = new Element('body'); document.head = new Element('head'); document.append(document.head, document.body);
  document.createElement = tag => new Element(tag); document.activeElement = document.body;
  const original = new Element('button'); original.className = 'viewer-folder-btn'; original.textContent = 'Open Folder'; original.setAttribute('onclick', 'openResultFolder()');
  const artist = new Element('button'); artist.className = 'viewer-folder-btn'; artist.textContent = 'Artist folder'; artist.setAttribute('onclick', 'openThumbnailFolder()'); document.body.append(original, artist);
  const items = options.items || [{id: 'a', name: 'first.png', width: 1024, height: 1024}, {id: 'b', name: 'second.png', width: 832, height: 1216}, {id: 'c', name: 'third.png', width: 512, height: 512}];
  let currentJob = null;
  const roots = [{id: 'output', label: '앱 출력 폴더', available: true}, {id: 'shared', label: '내장 저장공간', available: false}];
  async function request(action, data) {
    calls.push({action, data});
    if (options.request) { const result = await options.request(action, data); if (result !== undefined) return result; }
    if (action === 'state') return {roots, total: items.length, job: currentJob};
    if (action === 'list') return {items, page: 1, pages: 1, total: items.length};
    if (action === 'metadata') return {metadata: {prompt: '<script>alert(1)</script>', parameters: {steps: 28}}};
    if (action === 'browse') return {root_id: data.root_id, relative: data.relative, parent: data.relative ? '' : null, dirs: [{name: 'nested', relative: 'nested'}], files: [{name: 'one.png', relative: data.relative ? `${data.relative}/one.png` : 'one.png', size: 2048}, {name: 'two.png', relative: data.relative ? `${data.relative}/two.png` : 'two.png', size: 4096}], page: 1, pages: 1, total: 2};
    if (action === 'import') { currentJob = {id: 'job-1', state: 'running', processed: 0, imported: 0, duplicates: 0, failed: 0, total: data.files?.length ?? null}; return {job: currentJob}; }
    if (action === 'job') return {job: currentJob};
    if (action === 'cancel') { currentJob = {...currentJob, state: 'cancelled'}; return {job: currentJob}; }
    throw new Error('Unexpected action: ' + action);
  }
  const window = {
    AbortController,
    document, URL: {createObjectURL() { const value = 'blob:fixture-' + ++urlId; urls.add(value); return value; }, revokeObjectURL(value) { urls.delete(value); revoked.push(value); }},
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    MutationObserver: class { constructor(callback) { this.callback = callback; observers.push(this); } observe() {} disconnect() { this.closed = true; } },
    async fetch(url, init) { posts.push({url, init}); if (options.fetch) return options.fetch(url, init); return {ok: true, async json() { return {ok: true, history_id: 'inserted'}; }}; },
  };
  vm.runInNewContext(source, {window, module: {exports: {}}, console});
  const controller = window.__naiaInstallHistoryLibrary({request, async asset(id, kind) { assets.push({id, kind}); return options.asset ? options.asset(id, kind) : new Blob(['image'], {type: 'image/png'}); }});
  const buttons = () => document.querySelectorAll('button');
  const byText = text => { const target = buttons().find(value => value.textContent === text); assert.ok(target, `Missing button ${text}`); return target; };
  const checks = () => document.querySelectorAll('input').filter(value => value.type === 'checkbox');
  const byAria = label => { const target = document.querySelectorAll('[aria-label]').find(value => value.getAttribute('aria-label') === label); assert.ok(target, `Missing aria ${label}`); return target; };
  async function tick() { const first = timers.entries().next().value; if (first) { timers.delete(first[0]); await first[1](); await flush(); } }
  return {controller, document, window, original, artist, calls, assets, posts, urls, revoked, timers, observers, byText, byAria, checks, tick, items, Element};
}

test('job summary distinguishes imported, duplicate, failed and cancelled work', () => {
  const summary = jobSummary({state: 'completed', processed: 8, total: 8, imported: 3, duplicates: 2, failed: 3, skipped: 1});
  assert.match(summary, /추가 3/); assert.match(summary, /중복 2/); assert.match(summary, /실패 3/); assert.match(summary, /건너뜀 1/);
  assert.match(jobSummary({state: 'cancelled', processed: 1, imported: 1}), /취소됨/);
  assert.ok(!jobSummary({state: 'running', total: null}).includes(' / 0'));
});

test('comparison permits exactly two distinct IDs and metadata stays text', () => {
  const selection = new Map(); assert.ok(selectForComparison(selection, {id: 'one'}, true)); assert.ok(selectForComparison(selection, {id: 'two'}, true));
  assert.equal(selectForComparison(selection, {id: 'three'}, true), false); assert.equal(selection.size, 2);
  selectForComparison(selection, {id: 'one'}, false); assert.ok(selectForComparison(selection, {id: 'three'}, true));
  assert.match(metadataText({prompt: '<script>bad()</script>'}), /<script>bad/);
  assert.match(metadataText({prompt: 'x'.repeat(500)}, 20), /일부만/);
  const formatted = metadataText({prompt: 'portrait', negative: 'blur', width: 832, height: 1216, parameters: {seed: 0, steps: 28, sampler: 'k_euler', cfg_scale: 5, model: 'nai'}, info: {duplicate: 'DO_NOT_DUMP'}, extracted: {raw: 'DO_NOT_DUMP'}});
  assert.match(formatted, /프롬프트\nportrait/); assert.match(formatted, /네거티브 프롬프트\nblur/); assert.match(formatted, /시드: 0/); assert.match(formatted, /크기: 832 × 1216/); assert.ok(!formatted.includes('DO_NOT_DUMP'));
  assert.equal(isHistoryOpener({getAttribute: () => 'openThumbnailFolder()'}), false);
  assert.equal(isHistoryOpener({getAttribute: () => 'openResultFolder()'}), true);
});

test('work queue caps concurrent requests and clears pending work after view change', async () => {
  const queue = createTaskQueue(9); let active = 0, max = 0; const finish = [], started = [];
  const pending = [0, 1, 2, 3].map(index => queue.run(async () => { started.push(index); active++; max = Math.max(max, active); await new Promise(resolve => finish.push(resolve)); active--; }));
  await flush(); assert.equal(max, 2); assert.deepEqual(started, [0, 1]); queue.clear(); finish.splice(0).forEach(resolve => resolve()); await Promise.all(pending);
  assert.deepEqual(started, [0, 1]);
  await assert.rejects(queue.run(() => { throw new Error('failed'); }), /failed/); assert.equal(await queue.run(() => 42), 42); queue.close();
});

test('install replaces only history buttons, delegates clicks and restores on cleanup', async () => {
  const f = fixture(); assert.equal(f.original.textContent, '사진·폴더'); assert.equal(f.original.getAttribute('onclick'), null);
  assert.equal(f.artist.textContent, 'Artist folder'); assert.equal(f.artist.getAttribute('onclick'), 'openThumbnailFolder()');
  f.original.click(); await flush(); assert.equal(f.calls[0].action, 'state'); assert.ok(f.byText('사진 불러오기'));
  const next = new f.Element('button'); next.setAttribute('onclick', 'openResultFolder()'); next.textContent = 'folder'; f.document.body.append(next);
  f.observers[0].callback([{addedNodes: [next]}]); assert.equal(next.textContent, '사진·폴더');
  f.controller.destroy(); assert.equal(f.original.textContent, 'Open Folder'); assert.equal(f.original.getAttribute('onclick'), 'openResultFolder()'); assert.equal(f.urls.size, 0);
});

test('internal browser selects single or multiple files without a native file input', async () => {
  const f = fixture(); await f.controller.open(); f.byText('사진 불러오기').click(); f.byText('앱 출력 폴더열기').click(); await flush();
  assert.equal(f.document.querySelectorAll('input').some(value => value.type === 'file'), false);
  f.byAria('one.png 불러오기 선택').click(); assert.ok(!f.byText('선택한 사진 불러오기 (1)').disabled);
  f.byAria('two.png 불러오기 선택').click(); f.byText('선택한 사진 불러오기 (2)').click(); await flush();
  const submitted = f.calls.find(value => value.action === 'import'); assert.equal(submitted.data.root_id, 'output'); assert.deepEqual(Array.from(submitted.data.files), ['one.png', 'two.png']);
  assert.ok(f.byText('불러오기 취소')); assert.equal(f.timers.size, 1); f.controller.destroy();
});

test('whole-folder import defaults nonrecursive and explicitly accepts subfolders', async () => {
  for (const recursive of [false, true]) {
    const f = fixture(); await f.controller.open(); f.byText('사진 불러오기').click(); f.byText('앱 출력 폴더열기').click(); await flush();
    const subfolders = f.checks().find(value => value.parentElement.textContent === '하위 폴더 포함'); assert.equal(subfolders.checked, false);
    if (recursive) subfolders.click(); f.byText('이 폴더 전체').click(); await flush();
    const payload = f.calls.find(value => value.action === 'import').data; assert.equal(payload.whole_folder, true); assert.equal(payload.recursive, recursive); assert.equal(payload.files, undefined); f.controller.destroy();
  }
});

test('inaccessible roots are disabled and browse failures leave a working back button', async () => {
  const f = fixture({request: async action => action === 'browse' ? {ok: false, error: {code: 'permission_denied', message: '이 폴더에 접근할 수 없습니다.'}} : undefined});
  await f.controller.open(); f.byText('사진 불러오기').click(); assert.equal(f.byText('내장 저장공간현재 앱에서 접근할 수 없음').disabled, true);
  f.byText('앱 출력 폴더열기').click(); await flush(); assert.match(f.document.body.textContent, /이 폴더에 접근할 수 없습니다/);
  f.byText('폴더 목록으로').click(); assert.ok(f.byText('앱 출력 폴더열기')); f.controller.destroy();
});

test('closing stops polling without cancelling import and reopening resumes progress', async () => {
  const f = fixture(); await f.controller.open(); f.byText('사진 불러오기').click(); f.byText('앱 출력 폴더열기').click(); await flush(); f.byText('이 폴더 전체').click(); await flush();
  assert.equal(f.timers.size, 1); await f.tick(); assert.equal(f.calls.filter(value => value.action === 'job').length, 1);
  f.controller.close(); assert.equal(f.timers.size, 0); assert.equal(f.calls.filter(value => value.action === 'cancel').length, 0);
  await f.controller.open(); assert.equal(f.timers.size, 1); assert.match(f.document.body.textContent, /불러오는 중/);
  f.byText('불러오기 취소').click(); await flush(); assert.equal(f.calls.filter(value => value.action === 'cancel').length, 1); assert.match(f.document.body.textContent, /취소됨/); f.controller.destroy();
});

test('completion reports partial failures and refreshes the persistent gallery', async () => {
  const f = fixture({request: async action => action === 'job' ? {job: {id: 'job-1', state: 'completed', total: 5, processed: 5, imported: 2, duplicates: 1, failed: 2}} : undefined});
  await f.controller.open(); f.byText('사진 불러오기').click(); f.byText('앱 출력 폴더열기').click(); await flush(); f.byText('이 폴더 전체').click(); await flush(); await f.tick();
  assert.match(f.document.body.textContent, /추가 2 · 중복 1 · 실패 2/); assert.equal(f.timers.size, 0); assert.ok(f.calls.filter(value => value.action === 'list').length >= 3); f.controller.destroy();
});

test('two-photo comparison uses previews and never inserts into generated history', async () => {
  const f = fixture(); await f.controller.open(); await flush();
  f.byAria('first.png 비교 선택').click(); f.byAria('second.png 비교 선택').click(); f.byAria('third.png 비교 선택').click(); assert.equal(f.byAria('third.png 비교 선택').checked, false);
  f.byText('선택한 사진 비교 (2/2)').click(); await flush(); assert.equal(f.assets.filter(value => value.kind === 'preview').length, 2); assert.equal(f.assets.filter(value => value.kind === 'image').length, 0); assert.equal(f.posts.length, 0);
  assert.ok(f.document.querySelectorAll('dialog')[0].classList.contains('is-compare')); assert.equal(f.document.querySelectorAll('script').length, 0); assert.match(f.document.querySelectorAll('pre')[0].textContent, /<script>alert/); f.controller.destroy();
});

test('single detail posts the original exactly once after explicit history action', async () => {
  const f = fixture(); await f.controller.open(); f.byAria('first.png 크게 보기').click(); await flush(); assert.equal(f.posts.length, 0);
  const insert = f.byText('히스토리에 추가'); insert.click(); insert.click(); await flush();
  assert.equal(f.posts.length, 1); assert.equal(f.posts[0].init.method, 'POST'); assert.equal(f.posts[0].url, '/api/image/insert-history?label=first.png');
  assert.equal(f.assets.filter(value => value.kind === 'image').length, 1); assert.ok(f.byText('히스토리에 추가됨').disabled); f.controller.destroy();
});

test('uncertain POST outcome is not automatically retried', async () => {
  const f = fixture({fetch: async () => { throw new Error('connection lost'); }}); await f.controller.open(); f.byAria('first.png 크게 보기').click(); await flush(); f.byText('히스토리에 추가').click(); await flush();
  assert.equal(f.posts.length, 1); assert.ok(f.byText('히스토리에 추가').disabled); assert.match(f.document.body.textContent, /기존 히스토리를 확인/); f.controller.destroy();
});

test('history upload times out once and leaves an uncertain outcome visible', async () => {
  const f = fixture({fetch: async (_url, init) => new Promise((_resolve, reject) => { init.signal.addEventListener('abort', () => reject(new Error('aborted'))); })});
  await f.controller.open(); f.byAria('first.png 크게 보기').click(); await flush(); f.byText('히스토리에 추가').click(); await flush();
  assert.equal(f.posts.length, 1); assert.equal(f.timers.size, 1); await f.tick();
  assert.equal(f.posts.length, 1); assert.equal(f.timers.size, 0); assert.ok(f.byText('히스토리에 추가').disabled); assert.match(f.document.body.textContent, /기존 히스토리를 확인/); f.controller.destroy();
});

test('folder actions stay visible and become available when a background import finishes', async () => {
  const f = fixture({request: async action => action === 'job' ? {job: {id: 'job-1', state: 'completed', processed: 1, imported: 1}} : undefined});
  await f.controller.open(); f.byText('사진 불러오기').click(); f.byText('앱 출력 폴더열기').click(); await flush(); f.byText('이 폴더 전체').click(); await flush();
  f.byText('사진 불러오기').click(); f.byText('앱 출력 폴더열기').click(); await flush();
  assert.ok(f.byText('이 폴더 전체').disabled); assert.ok(f.byText('이 폴더 전체').parentElement.classList.contains('naia-hl-sticky-actions')); await f.tick();
  assert.equal(f.byText('이 폴더 전체').disabled, false); f.controller.destroy();
});

test('closing a view prevents late asset creation and releases existing object URLs', async () => {
  const finish = []; const f = fixture({asset: () => new Promise(resolve => finish.push(resolve))});
  await f.controller.open(); await flush(); assert.equal(finish.length, 2); f.controller.close(); finish.splice(0).forEach(resolve => resolve(new Blob(['image']))); await flush();
  assert.equal(f.urls.size, 0); assert.equal(f.revoked.length, 0); f.controller.destroy();
  const live = fixture(); await live.controller.open(); await flush(); assert.ok(live.urls.size > 0); live.controller.close(); assert.equal(live.urls.size, 0); assert.ok(live.revoked.length > 0); live.controller.destroy();
});
