import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import {webcrypto} from 'node:crypto';

const script = fs.readFileSync(new URL('../naia_screen_agent.js', import.meta.url), 'utf8');

// Small DOM test double: transport, stale-target and policy tests use no network,
// generation backend, browser account or private files.
class Element {
  constructor(tag = 'BUTTON', attrs = {}, parent = null) {
    this.nodeType = 1; this.tagName = tag; this.attrs = {...attrs}; this.parentElement = parent;
    this.id = attrs.id || ''; this.type = attrs.type || (tag === 'INPUT' ? 'text' : '');
    this.textContent = attrs.label || this.id; this.children = []; this.value = '';
    this.hidden = false; this.isConnected = true; this.disabled = false; this.checked = false;
    this.maxLength = -1; this.min = ''; this.max = ''; this.scrollTop = this.scrollLeft = 0;
    this.clientWidth = this.scrollWidth = 100; this.clientHeight = this.scrollHeight = 24;
    this.rect = {x: 10, y: 10, left: 10, top: 10, right: 110, bottom: 34, width: 100, height: 24};
    this.classList = {contains: value => (this.attrs.class || '').split(' ').includes(value)};
    this.clicks = 0; this.events = [];
    if (parent) parent.children.push(this);
  }
  getAttribute(key) { return key in this.attrs ? String(this.attrs[key]) : null; }
  hasAttribute(key) { return key in this.attrs; }
  matches(selectors) {
    return selectors.split(',').some(selector => {
      selector = selector.trim();
      const tag = selector.match(/^[A-Za-z]+/);
      if (tag && tag[0].toUpperCase() !== this.tagName) return false;
      for (const m of selector.matchAll(/#([\w-]+)/g)) if (this.id !== m[1]) return false;
      for (const m of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(m[1])) return false;
      for (const m of selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
        if (m[1] === 'hidden' ? !this.hidden : !this.hasAttribute(m[1])) return false;
        if (m[2] !== undefined && this.getAttribute(m[1]) !== m[2]) return false;
      }
      return true;
    });
  }
  closest(selector) { for (let n = this; n; n = n.parentElement) if (n.matches(selector)) return n; return null; }
  contains(other) { for (let n = other; n; n = n.parentElement) if (n === this) return true; return false; }
  getBoundingClientRect() { return this.rect; }
  click() { this.clicks++; if (this.type === 'checkbox') this.checked = !this.checked; }
  dispatchEvent(event) { this.events.push(event.type); }
  scrollBy({top, left}) { this.scrollTop += top; this.scrollLeft += left; }
}
class Input extends Element { get value() { return this._value || ''; } set value(value) { this._value = value; } }
class Select extends Input {
  constructor(attrs, parent) { super('SELECT', attrs, parent); this.options = []; }
  get value() { return this._value || ''; } set value(value) { this._value = value; }
}
class Textarea extends Input { get value() { return this._value || ''; } set value(value) { this._value = value; } }

function fixture() {
  const body = new Element('BODY'), elements = [], messages = [], listeners = new Map(), observers = [];
  let clock = 0;
  body.id = ''; body.rect = {x: 0, y: 0, left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800};
  const doc = {
    body,
    querySelectorAll: selector => elements.filter(el => el.matches(selector)),
    createTreeWalker: () => ({nextNode: () => false}),
    elementFromPoint: (x, y) => doc.cover || elements.findLast(el => !el.hidden && !el.closest('[aria-hidden="true"]') && x >= el.rect.left && x <= el.rect.right && y >= el.rect.top && y <= el.rect.bottom) || body,
    getElementById: id => elements.find(el => el.id === id),
    addEventListener: (name, handler) => listeners.set('doc:' + name, handler), removeEventListener() {},
  };
  const window = {
    document: doc, innerWidth: 1000, innerHeight: 800, scrollX: 0, scrollY: 0, crypto: webcrypto,
    performance: {now: () => clock},
    getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: '1', overflow: 'visible', overflowX: 'visible', overflowY: 'visible'}),
    HTMLInputElement: Input, HTMLSelectElement: Select, HTMLTextAreaElement: Textarea,
    Event: class { constructor(type) { this.type = type; } },
    MutationObserver: class { constructor(callback) { this.callback = callback; this.records = []; observers.push(this); } observe() {} disconnect() {} takeRecords() { return this.records.splice(0); } },
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: name => listeners.delete(name), setTimeout: callback => setImmediate(callback),
    scrollBy({top, left}) { this.scrollX += left; this.scrollY += top; },
  };
  const context = vm.createContext({window, globalThis: window, module: {exports: {}}, URL, TextEncoder, Uint32Array, console});
  vm.runInContext(script, context);
  function add(tag, attrs = {}, parent = body) {
    const Class = tag === 'INPUT' ? Input : tag === 'TEXTAREA' ? Textarea : tag === 'SELECT' ? Select : Element;
    const el = tag === 'SELECT' ? new Class(attrs, parent) : new Class(tag, attrs, parent);
    const y = elements.length * 35 + 10;
    el.rect = {...el.rect, y, top: y, bottom: y + 24};
    if (parent.children.length > 1) el.previousElementSibling = parent.children.at(-2);
    elements.push(el); return el;
  }
  const frame = add('IFRAME'); frame.hidden = true;
  frame.contentWindow = {postMessage: (message, origin) => messages.push({message, origin})};
  window.__naiaInstallScreenAgent({frame, panelOrigin: 'http://127.0.0.1:4321'});
  const permissions = {screen: true, control: true, edit: true, generate: false};
  let nextId = 0;
  async function call(name, args = {}, perms = permissions, extra = {}) {
    const id = 'command-' + ++nextId;
    listeners.get('message')({source: frame.contentWindow, origin: 'http://127.0.0.1:4321', data: {type: 'naia-ui-command', id, name, arguments: args, permissions: perms}, ...extra});
    for (let attempts = 0; attempts < 10; attempts++) { await new Promise(resolve => setImmediate(resolve)); const result = messages.find(m => m.message.id === id); if (result) return JSON.parse(JSON.stringify(result.message)); }
    return undefined;
  }
  return {add, call, doc, window, frame, messages, listeners, observers, permissions,
    advanceTime: ms => { clock += ms; }, policy: context.module.exports.policy, optionAllowed: context.module.exports.optionAllowed};
}

test('only the exact trusted panel can send commands; handshake is acknowledged', async () => {
  const f = fixture();
  assert.equal(await f.call('naia_get_screen', {}, undefined, {origin: 'https://attacker.example'}), undefined);
  assert.equal(await f.call('naia_get_screen', {}, undefined, {source: {}}), undefined);
  f.listeners.get('message')({source: f.frame.contentWindow, origin: 'http://127.0.0.1:4321', data: {type: 'naia-android-ready'}});
  assert.equal(f.messages.at(-1).message.type, 'naia-ui-ready');
});

test('screen and edit are separate permissions; generic generation stays blocked', async () => {
  const f = fixture();
  f.add('TEXTAREA', {id: 'promptEdit'});
  f.add('BUTTON', {id: 'btnGen', onclick: 'generateAction()'});
  assert.equal((await f.call('naia_get_screen', {}, {screen: false})).result.error.code, 'screen_permission_required');
  const response = await f.call('naia_get_screen', {}, {...f.permissions, edit: false, generate: true});
  assert.equal(response.result.controls.find(c => c.label === 'promptEdit').blocked_reason, 'edit_permission_required');
  assert.equal(response.result.controls.find(c => c.label === 'btnGen').actions.length, 0);
  const target = response.result.controls.find(c => c.label === 'btnGen').target;
  const action = await f.call('naia_ui_action', {snapshot_id: response.result.snapshot_id, action: 'click', target}, {...f.permissions, generate: true});
  assert.equal(action.is_error, true);
});

test('field edits dispatch input and change; successful action consumes token', async () => {
  const f = fixture(), prompt = f.add('TEXTAREA', {id: 'promptEdit'});
  const {result: screen} = await f.call('naia_get_screen');
  const action = {snapshot_id: screen.snapshot_id, target: screen.controls[0].target, action: 'fill', value: 'two people'};
  const result = await f.call('naia_ui_action', action);
  assert.equal(result.is_error, false); assert.equal(prompt.value, 'two people');
  assert.deepEqual(prompt.events, ['input', 'change']); assert.equal(result.result.image, undefined);
  assert.equal((await f.call('naia_ui_action', action)).result.error.code, 'stale_snapshot');
});

test('read/search field fill still requires edit, matching broker policy', async () => {
  const f = fixture(); f.add('INPUT', {id: 'artistThumbSearch'});
  const {result: screen} = await f.call('naia_get_screen');
  const result = await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, target: screen.controls[0].target, action: 'fill', value: 'test'}, {...f.permissions, edit: false});
  assert.equal(result.result.error.code, 'edit_permission_required');
});

test('DOM mutation, changed value, and replaced target each reject stale action', async () => {
  for (const mode of ['mutation', 'value', 'removed']) {
    const f = fixture(), input = f.add('INPUT', {id: 'pSeed'});
    const {result: screen} = await f.call('naia_get_screen');
    if (mode === 'mutation') { input.attrs.class = 'changed'; f.observers[0].records.push({type: 'attributes', target: input, attributeName: 'class', oldValue: null}); }
    if (mode === 'value') input.value = '123';
    if (mode === 'removed') input.isConnected = false;
    const result = await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, target: screen.controls[0].target, action: 'fill', value: '9'});
    assert.match(result.result.error.code, /^stale_/);
  }
});

test('identical custom select refresh mutation does not stale snapshot', async () => {
  const f = fixture(), button = f.add('BUTTON', {onclick: "switchTab('params')", class: 'tab'});
  const {result: screen} = await f.call('naia_get_screen');
  f.observers[0].records.push({type: 'attributes', target: button, attributeName: 'class', oldValue: 'tab'});
  const result = await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, target: screen.controls[0].target, action: 'click'});
  assert.equal(result.is_error, false); assert.equal(button.clicks, 1);
});

test('covered controls cannot be clicked through an overlay', async () => {
  const f = fixture(), button = f.add('BUTTON', {onclick: "switchTab('params')"});
  const {result: screen} = await f.call('naia_get_screen');
  f.doc.cover = new Element('DIV');
  const result = await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, target: screen.controls[0].target, action: 'click'});
  assert.equal(result.result.error.code, 'covered_control'); assert.equal(button.clicks, 0);
});

test('protected settings reject both snapshot and capture before rendering', async () => {
  const f = fixture(); f.add('DIV', {id: 'rightTabSettings'});
  let captures = 0; f.window.html2canvas = () => { captures++; };
  for (const capture of [false, true]) assert.equal((await f.call('naia_get_screen', {capture})).result.error.code, 'protected_screen');
  assert.equal(captures, 0);
});

test('custom select adapter uses native value, blocks artist open-folder sentinel', async () => {
  const f = fixture();
  const select = f.add('SELECT', {id: 'artistThumbMode', class: 'native-select-hidden', 'aria-hidden': 'true'});
  select.options = [{value: 'regular', label: 'Regular'}, {value: '__artist_thumb_open_folder__', label: 'Open folder'}];
  const wrapper = f.add('DIV', {class: 'custom-select'}); wrapper.previousElementSibling = select;
  const button = f.add('BUTTON', {class: 'custom-select-button'}, wrapper);
  const {result: screen} = await f.call('naia_get_screen');
  const field = screen.controls.find(c => c.role === 'combobox');
  assert.deepEqual(field.options.map(o => o.value), ['regular']);
  assert.equal((await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, target: field.target, action: 'select', value: '__artist_thumb_open_folder__'})).result.error.code, 'invalid_option');
  const next = (await f.call('naia_get_screen')).result;
  assert.equal((await f.call('naia_ui_action', {snapshot_id: next.snapshot_id, target: next.controls.find(c => c.role === 'combobox').target, action: 'select', value: 'regular'})).is_error, false);
  assert.equal(select.value, 'regular'); assert.equal(button.clicks, 0);
});

test('studio allows audited navigation/edits, blocks generation/files/deletion and unknown buttons', () => {
  const f = fixture(), studio = f.add('DIV', {id: 'studioRoot'});
  for (const action of ['toggle-global', 'close-editor']) assert.equal(f.policy(f.add('BUTTON', {'data-studio-action': action}, studio)).edit, false);
  for (const action of ['add-frame', 'apply-current']) assert.equal(f.policy(f.add('BUTTON', {'data-studio-action': action}, studio)).edit, true);
  for (const action of ['generate-selected', 'start-sequence', 'export-board', 'import-board', 'delete-frame', 'surprise']) assert.equal(f.policy(f.add('BUTTON', {'data-studio-action': action}, studio)).actions.length, 0);
});

test('scroll accepts schema maximum 2000 and cannot reuse a consumed token', async () => {
  const f = fixture(); const screen = (await f.call('naia_get_screen')).result;
  const response = await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, action: 'scroll', direction: 'down', amount: 2000});
  assert.equal(response.is_error, false); assert.equal(f.window.scrollY, 2000);
});

test('capture bounds canvas at1600, ignores sensitive fields/iframes, releases canvas', async () => {
  const f = fixture(); f.window.innerWidth = 2400; f.window.innerHeight = 1800;
  let options; const canvas = {width: 1600, height: 1200, toDataURL: () => 'data:image/jpeg;base64,/9j/2Q=='};
  f.window.html2canvas = async (_body, value) => { options = value; return canvas; };
  const response = await f.call('naia_get_screen', {capture: true});
  assert.equal(response.is_error, false); assert.equal(response.result.image.width, 1600);
  assert.equal(options.scale, 2 / 3); assert.equal(options.allowTaint, false);
  assert.equal(options.ignoreElements(new Element('IFRAME')), true);
  assert.equal(options.ignoreElements(new Input('INPUT', {type: 'password'})), true);
  assert.equal(canvas.width, 0); assert.equal(canvas.height, 0);
});


test('unrelated live DOM updates preserve Quick snapshot through queued and delivered mutations', async () => {
  const f = fixture(), quick = f.add('BUTTON', {id: 'tagFilterToggle', onclick: 'toggleTagFilter()'});
  const counter = f.add('SPAN', {id: 'liveCounter'});
  const {result: screen} = await f.call('naia_get_screen');
  counter.attrs.title = 'updated';
  const record = {type: 'attributes', target: counter, attributeName: 'title', oldValue: null};
  f.observers[0].callback([record]);
  f.observers[0].records.push(record);
  const response = await f.call('naia_ui_action', {snapshot_id: screen.snapshot_id, target: screen.controls[0].target, action: 'click'});
  assert.equal(response.is_error, false); assert.equal(quick.clicks, 1);
});

test('actual target and ancestor changes still reject saved navigation clicks', async () => {
  for (const mode of ['ancestor', 'handler']) {
    const f = fixture(), parent = f.add('DIV'), quick = f.add('BUTTON', {onclick: 'toggleTagFilter()'}, parent);
    const {result: screen} = await f.call('naia_get_screen');
    if (mode === 'ancestor') { parent.attrs.class = 'new-context'; f.observers[0].records.push({type:'attributes', target:parent, attributeName:'class', oldValue:null}); }
    if (mode === 'handler') quick.attrs.onclick = 'generateAction()';
    const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
    assert.match(response.result.error.code, /^stale_/); assert.equal(quick.clicks, 0);
  }
});

test('Android view switch works without edit permission; unsafe lookalikes stay blocked', async () => {
  const f = fixture(), button = f.add('BUTTON', {label:'간편 화면 ▾', title:'간편 화면 사용 중. 전체 기능을 보려면 전체 화면으로 전환'});
  const {result: screen} = await f.call('naia_get_screen', {}, {...f.permissions, edit:false});
  const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'}, {...f.permissions, edit:false});
  assert.equal(response.is_error, false); assert.equal(button.clicks, 1);
  for (const onclick of ['generateAction()', 'deleteFrame()', 'openSaveDirectoryPanel()', 'openApiPopup()']) {
    assert.deepEqual(f.policy(f.add('BUTTON', {label:'전체 화면', onclick})).actions.length, 0);
  }
  assert.equal(f.policy(f.add('BUTTON', {label:'Unknown action'})).actions.length, 0);
});

test('standard navigation destinations allow tabs but never settings', () => {
  const f = fixture(); f.add('DIV', {id:'galleryPanel'}); f.add('DIV', {id:'settingsRoot'});
  assert.equal(f.policy(f.add('BUTTON', {role:'tab', 'aria-controls':'galleryPanel'})).actions[0], 'click');
  assert.equal(f.policy(f.add('BUTTON', {role:'tab', 'aria-controls':'settingsRoot'})).actions.length, 0);
});

test('new protected screen blocks action even when original button is unchanged', async () => {
  const f = fixture(); f.add('BUTTON', {onclick:'toggleTagFilter()'});
  const {result: screen} = await f.call('naia_get_screen');
  f.add('DIV', {id:'settingsRoot'});
  const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
  assert.equal(response.result.error.code, 'protected_screen');
});


test('branded Android shell view switch uses exact observed title and reports agent version', async () => {
  const f = fixture(), button = f.add('DIV', {role:'button', label:'NAIA2 간편 화면 ▾', title:'간편 화면 사용 중. 전체 기능을 보려면 전체 화면으로 전환'});
  const {result: screen} = await f.call('naia_get_screen');
  assert.equal(screen.agent_version, '0.2.4');
  const result = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id,target:screen.controls[0].target,action:'click'});
  assert.equal(result.is_error, false);assert.equal(button.clicks,1);
});


test('trusted automatic scroll and resize keep stable Quick target; moved target is rejected', async () => {
  for (const moved of [false,true]) {
    const f=fixture(), button=f.add('BUTTON',{onclick:'toggleTagFilter()'});
    const {result:screen}=await f.call('naia_get_screen');
    f.listeners.get('doc:scroll')({isTrusted:true});f.listeners.get('resize')({isTrusted:true});
    if(moved)button.rect={...button.rect,x:20,left:20,right:120};
    const result=await f.call('naia_ui_action',{snapshot_id:screen.snapshot_id,target:screen.controls[0].target,action:'click'});
    if(moved){assert.equal(result.result.error.code,'stale_target');assert.equal(button.clicks,0);}
    else {assert.equal(result.is_error,false);assert.equal(button.clicks,1);}
  }
});

test('overlapping screen reads preserve prior navigation, then consume all old snapshots', async () => {
  const f = fixture(), quick = f.add('BUTTON', {onclick:'openFnQuick()'});
  const [first, second] = await Promise.all([f.call('naia_get_screen'), f.call('naia_get_screen')]);
  const action = screen => ({snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
  assert.equal((await f.call('naia_ui_action', action(first.result))).is_error, false);
  assert.equal((await f.call('naia_ui_action', action(second.result))).result.error.code, 'stale_snapshot');
  assert.equal(quick.clicks, 1);
});

test('unrelated trusted touch and keyboard input preserve only unchanged navigation', async () => {
  for (const event of ['pointerdown', 'keydown']) {
    const f = fixture(), quick = f.add('BUTTON', {onclick:'openFnQuick()'});
    const screen = (await f.call('naia_get_screen')).result;
    f.listeners.get('doc:' + event)({isTrusted:true, target:f.doc.body});
    const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
    assert.equal(response.is_error, false); assert.equal(quick.clicks, 1);
  }
});

test('trusted input still rejects field edits, edit buttons and whole-page scroll', async () => {
  for (const mode of ['fill', 'edit-click', 'page-scroll']) {
    const f = fixture();
    const target = mode === 'fill' ? f.add('TEXTAREA', {id:'promptEdit'}) : f.add('BUTTON', {id:'artistThumbInsertBtn'});
    const screen = (await f.call('naia_get_screen')).result;
    f.listeners.get('doc:keydown')({isTrusted:true, target:f.doc.body});
    const args = {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:mode === 'fill' ? 'fill' : 'click'};
    if (mode === 'fill') args.value = 'changed';
    if (mode === 'page-scroll') { delete args.target; args.action = 'scroll'; args.direction = 'down'; }
    const response = await f.call('naia_ui_action', args);
    assert.equal(response.result.error.code, 'stale_user_input');
    assert.equal(target.value, ''); assert.equal(target.clicks, 0); assert.equal(f.window.scrollY, 0);
  }
});

test('trusted input allows bounded target scroll only while its exact state is unchanged', async () => {
  for (const moved of [false, true]) {
    const f = fixture(), panel = f.add('DIV', {id:'thumbnailPanel'});
    panel.scrollHeight = 1000;
    f.window.getComputedStyle = el => ({display:'block', visibility:'visible', opacity:'1', overflow:el === panel ? 'auto' : 'visible', overflowX:'visible', overflowY:'visible'});
    const screen = (await f.call('naia_get_screen')).result;
    const area = screen.controls.find(c => c.role === 'scrollarea');
    assert.ok(area);
    f.listeners.get('doc:pointerdown')({isTrusted:true, target:f.doc.body});
    if (moved) panel.scrollTop = 10;
    const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:area.target, action:'scroll', direction:'down', amount:50});
    if (moved) { assert.equal(response.result.error.code, 'stale_target'); assert.equal(panel.scrollTop, 10); }
    else { assert.equal(response.is_error, false); assert.equal(panel.scrollTop, 50); }
  }
});

test('mutations dirty earlier snapshots even after a newer read', async () => {
  const f = fixture(), quick = f.add('BUTTON', {onclick:'openFnQuick()'});
  const first = (await f.call('naia_get_screen')).result;
  quick.attrs.class = 'changed';
  f.observers[0].records.push({type:'attributes', target:quick, attributeName:'class', oldValue:null});
  await f.call('naia_get_screen');
  const response = await f.call('naia_ui_action', {snapshot_id:first.snapshot_id, target:first.controls[0].target, action:'click'});
  assert.equal(response.result.error.code, 'stale_target'); assert.equal(quick.clicks, 0);
});

test('snapshots expire after 120 seconds and only the most recent four are retained', async () => {
  for (const elapsed of [119999, 120000]) {
    const f = fixture(), quick = f.add('BUTTON', {onclick:'openFnQuick()'});
    const screen = (await f.call('naia_get_screen')).result;
    f.advanceTime(elapsed);
    const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
    assert.equal(response.is_error, elapsed === 120000);
    assert.equal(quick.clicks, elapsed === 120000 ? 0 : 1);
  }
  for (const count of [4, 5]) {
    const f = fixture(), quick = f.add('BUTTON', {onclick:'openFnQuick()'});
    const screens = [];
    for (let i = 0; i < count; i++) screens.push((await f.call('naia_get_screen')).result);
    const response = await f.call('naia_ui_action', {snapshot_id:screens[0].snapshot_id, target:screens[0].controls[0].target, action:'click'});
    assert.equal(response.is_error, count === 5); assert.equal(quick.clicks, count === 5 ? 0 : 1);
  }
});

test('failed read preserves a safe snapshot, protected-screen read clears it', async () => {
  for (const protectedView of [false, true]) {
    const f = fixture(), quick = f.add('BUTTON', {onclick:'openFnQuick()'});
    const screen = (await f.call('naia_get_screen')).result;
    const settings = protectedView ? f.add('DIV', {id:'settingsRoot'}) : null;
    const failed = await f.call('naia_get_screen', {capture:true});
    assert.equal(failed.is_error, true);
    if (settings) settings.hidden = true;
    const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
    assert.equal(response.is_error, protectedView); assert.equal(quick.clicks, protectedView ? 0 : 1);
  }
});

test('focusable Android switch accepts normalized exact title but blocks unsafe lookalikes', async () => {
  for (const title of ['  간편 화면 사용 중.\n 전체 기능을 보려면 전체 화면으로 전환  ', '전체 화면 사용 중.\t 간편 화면으로 전환']) {
    const f = fixture(), button = f.add('DIV', {tabindex:'0', label:'NAIA2 ▾', title});
    const screen = (await f.call('naia_get_screen')).result;
    const response = await f.call('naia_ui_action', {snapshot_id:screen.snapshot_id, target:screen.controls[0].target, action:'click'});
    assert.equal(response.is_error, false); assert.equal(button.clicks, 1);
    const form = f.add('FORM');
    assert.equal(f.policy(f.add('DIV', {tabindex:'0', title}, form)).actions.length, 0);
    assert.equal(f.policy(f.add('DIV', {tabindex:'0', title, onclick:'generateAction()'})).actions.length, 0);
    assert.equal(f.policy(f.add('DIV', {tabindex:'0', title:title + ' more'})).actions.length, 0);
    assert.equal(f.policy(f.add('DIV', {title})).actions.length, 0);
  }
});
