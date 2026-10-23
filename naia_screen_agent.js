/* NAIA screen bridge. Audited against the pinned remote UI, not a DOM/eval API.
 * Unknown controls deliberately remain visible but non-actionable. Generation,
 * files and account/settings entry points are never generic UI actions.
 */
(function (global) {
  'use strict';
  const MAX_CONTROLS = 160;
  const MAX_SNAPSHOTS = 4, SNAPSHOT_TTL_MS = 120000;
  const SENSITIVE_ROOTS = '#apiPopupOverlay,#setupDialog,#rightTabSettings,#settingsRoot,#naiAcctPop,' +
    '#webuiCustomPayloadPopup,#peDebugPanel,#naiModelManagerPanel,' +
    '[data-naia-sensitive],dialog[aria-label="Android 도구 · 플러그인"]';
  const SECRET_FIELDS = 'input[type="password"],input[type="file"],[autocomplete="current-password"],' +
    '[autocomplete="new-password"],[data-naia-private]';
  const INTERACTIVE = 'button,input,textarea,select,a,[role="button"],[role="tab"],[onclick],[tabindex]';
  const NAV_IDS = new Set(('artistThumbPrevBtn artistThumbNextBtn artistThumbGotoBtn artistThumbSelectBtn ' +
    'artistThumbRandomBtn artistThumbResultExpand artistThumbResultClose thumbPreviewClose ' +
    'characterViewerPrevBtn characterViewerNextBtn characterViewerGotoBtn characterViewerResultExpand ' +
    'characterViewerResultClose').split(' '));
  const EDIT_IDS = new Set(['artistThumbInsertBtn', 'thumbPreviewInsert']);
  const READ_FIELDS = new Set(('artistThumbSearch artistThumbFilter artistThumbMode artistThumbGotoInput ' +
    'characterViewerSearch characterViewerGroupSearch characterViewerGotoInput ' +
    'characterViewerThumbFirst characterViewerFavOnly').split(' '));
  const EDIT_FIELDS = new Set(('promptEdit negEdit pModel pSampler pScheduler pResolution qResolution pSteps ' +
    'pCfgScale pCfgRescale pSeed pAnimaWeight pEnableHr pHrScale pHrUpscaler pDenoise pHiresSteps pHrCfg pRescaleCfg ' +
    'artistThumbPrefix artistThumbPositive artistThumbWeightSlider artistThumbWeightInput artistThumbPostfix ' +
    'characterViewerVariant characterViewerPrompt characterViewerPrefix characterViewerPostfix characterViewerAutoCopyright ' +
    'characterViewerAutoCharacteristics characterViewerHideName characterViewerCosplay characterViewerCosplayName').split(' '));
  const NAV_HANDLERS = new Set(([
    "switchTab('prompt')", "switchTab('params')", "switchTab('negative')",
    "switchRightTab('result')", "switchRightTab('pngInfo')", "switchRightTab('thumb')",
    "switchRightTab('characters')", "switchRightTab('charAssets')", "switchRightTab('danbooru')",
    "switchRightTab('studio')", 'requestArtistsTab()', 'toggleDrawer()', 'toggleHistoryRail()',
    'toggleMobileHistoryRail()', 'toggleFnMenu(event)', 'dismissFnQuick(event)', 'openFnQuick()',
    'openFnPreset()', 'openFnSequence()', 'openFnISequence()', 'openFnV5Scene()',
    'closeModule()', 'toggleTagFilter()', 'closeTagFilter()', 'refreshMetadataViewer()',
    'openViewerPopup()', 'closeChunkPanel()', 'toggleTagFilterPresets()',
  ]).map(normalizeHandler));
  const EDIT_HANDLERS = new Set(([
    'toggleTransparentBackground()', 'toggleSeedResLock()',
    "toggleOptionButton('prompt_fixed')", "toggleOptionButton('wildcard_standalone')",
    "toggleQuickFlag(this,'random_resolution')", "toggleQuickFlag(this,'auto_fit_resolution')",
  ]).map(normalizeHandler));
  const STUDIO_NAV = new Set(['toggle-global', 'close-editor', 'toggle-import']);
  const STUDIO_EDIT = new Set(['sync-current', 'apply-current', 'add-frame', 'duplicate-frame', 'capture-current-new']);
  const STUDIO_GLOBAL = new Set(['globalResolution', 'repeat', 'seedMode', 'prefix', 'postfix', 'globalNegative']);
  const STUDIO_FRAME = new Set(['enabled', 'prompt', 'negative', 'dialogue', 'name', 'resolution', 'cfgScale']);
  const BLOCK_NAMES = /(?:generate|autogen|generation|upscale|enhance|preview45Run|queue|download|delete|remove|export|import-board|reset|clear-frame|save|folder|directory|clipboard|copy|remote|login|logout|auth|token|password|secret|account|extension|plugin|setup|settings|api|payload|migration)/i;
  const PROTECTED_NAME = /(?:api|setup|setting|account|auth|login|logout|token|secret|password|extension|plugin|directory|storage|cloudflared|migration)/i;
  const norm = (v, max) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, max);
  function normalizeHandler(value) { return String(value || '').replace(/\s+/g, '').replace(/;$/, ''); }
  function error(code) { const e = new Error(code); e.code = code; return e; }
  function attr(el, key) { return el.getAttribute(key) || ''; }
  function sensitive(el) {
    if (!el || el.nodeType !== 1) return false;
    return !!el.closest(SENSITIVE_ROOTS + ',' + SECRET_FIELDS) ||
      PROTECTED_NAME.test([el.id, attr(el, 'name'), attr(el, 'autocomplete')].join(' '));
  }
  function fieldPolicy(el, edit) {
    if (el.tagName === 'SELECT') return {actions: ['select'], edit: true};
    if (el.tagName === 'TEXTAREA') return {actions: ['fill'], edit: true};
    if (el.tagName === 'INPUT' && ['text', 'search', 'number', 'range', ''].includes(el.type)) return {actions: ['fill'], edit: true};
    if (el.tagName === 'INPUT' && ['checkbox', 'radio'].includes(el.type)) return {actions: ['click'], edit};
    return {actions: [], reason: 'unsupported_control'};
  }
  function policy(el) {
    if (sensitive(el)) return {actions: [], reason: 'protected_settings'};
    const id = el.id || '';
    const handler = normalizeHandler(attr(el, 'onclick'));
    if (NAV_IDS.has(id) || NAV_HANDLERS.has(handler)) return {actions: ['click'], edit: false};
    if (EDIT_IDS.has(id) || EDIT_HANDLERS.has(handler)) return {actions: ['click'], edit: true};
    if (READ_FIELDS.has(id)) return fieldPolicy(el, false);
    if (EDIT_FIELDS.has(id)) return fieldPolicy(el, true);
    if (el.closest('#studioRoot')) {
      if (el.hasAttribute('data-studio-frame')) return {actions: ['click'], edit: false};
      const action = attr(el, 'data-studio-action');
      if (STUDIO_NAV.has(action)) return {actions: ['click'], edit: false};
      if (STUDIO_EDIT.has(action)) return {actions: ['click'], edit: true};
      if (STUDIO_GLOBAL.has(attr(el, 'data-studio-global')) || STUDIO_FRAME.has(attr(el, 'data-studio-frame-field'))) return fieldPolicy(el, true);
    }
    // Selecting an artist changes its local prompt preview and may change a mix.
    if (el.matches('.artist-thumb-card[data-artist]') && el.closest('#artistThumbGrid')) return {actions: ['click'], edit: true};
    if (el.matches('.character-viewer-group-item[data-group]') && el.closest('#characterViewerGroupList')) return {actions: ['click'], edit: false};
    if ((el.matches('.character-viewer-card[data-group][data-character]') && el.closest('#characterViewerGrid')) ||
        (el.matches('.character-viewer-list-item[data-group][data-character]') && el.closest('#characterViewerList'))) return {actions: ['click'], edit: true};
    if (el.hasAttribute('data-character-viewer-tab') && el.closest('#rightTabCharacters')) return {actions: ['click'], edit: false};
    if (el.hasAttribute('data-metadata-detail-tab') && ['params', 'raw'].includes(attr(el, 'data-metadata-detail-tab'))) return {actions: ['click'], edit: false};
    const identifiers = [id, handler, attr(el, 'data-studio-action'), attr(el, 'data-action'), attr(el, 'data-artist-batch-mode')].join(' ');
    // Presentation-only controls may be added by the Android shell. Never
    // infer arbitrary button safety from the absence of a blocked word.
    if (!BLOCK_NAMES.test(identifiers) && !el.closest('form')) {
      // The Android shell also renders this audited switch as a focusable DIV.
      const title = norm(attr(el, 'title'), 300);
      const shellViewTitle = /^(?:간편 화면 사용 중\. 전체 기능을 보려면 전체 화면으로 전환|전체 화면 사용 중\. 간편 화면으로 전환)$/.test(title);
      if (shellViewTitle && el.matches('button,[role="button"],[onclick],[tabindex]')) return {actions: ['click'], edit: false};
      if (!el.matches('button,[role="button"],[onclick]')) return {actions: [], reason: 'unsupported_control'};
      const text = norm(el.textContent, 100).replace(/[▾▴▼▲⛶]/g, '').trim();
      const viewLabels = /^(?:간편 화면|전체 화면|전체화면|전체 화면으로 전환|간편 화면으로 전환|크게 보기|접기|펼치기|Full screen|Fullscreen|Simple view)$/i;
      if (viewLabels.test(text)) return {actions: ['click'], edit: false};
      // Standard tab/disclosure navigation must identify its local destination.
      const destination = attr(el, 'aria-controls');
      const panel = destination && global.document.getElementById?.(destination);
      if (panel && !sensitive(panel) && !panel.querySelector?.(SENSITIVE_ROOTS + ',' + SECRET_FIELDS) &&
          (attr(el, 'role') === 'tab' || ['true', 'false'].includes(attr(el, 'aria-expanded')))) {
        return {actions: ['click'], edit: false};
      }
    }
    return {actions: [], reason: BLOCK_NAMES.test(identifiers) ? 'restricted_action' : 'unsupported_control'};
  }
  function optionAllowed(el, option) {
    return !option.disabled && !(option.parentElement && option.parentElement.disabled) &&
      !(el.id === 'artistThumbMode' && option.value === '__artist_thumb_open_folder__');
  }
  // Upstream customSelects hides the native SELECT and inserts this exact
  // sibling button. Expose the same select operation against its visible face,
  // without granting generic clicks on a popup option or settings dropdown.
  function sourceElement(el) {
    if (!el.matches('.custom-select-button') || !el.parentElement.matches('.custom-select')) return el;
    const select = el.parentElement.previousElementSibling;
    return select && select.tagName === 'SELECT' && select.classList.contains('native-select-hidden') ? select : el;
  }
  function visible(el) {
    if (!el || !el.isConnected || el.hidden || el.closest('[hidden],[aria-hidden="true"]')) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= global.innerHeight || rect.left >= global.innerWidth) return false;
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = global.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      if (node !== el && /(auto|scroll|hidden|clip)/.test(style.overflow + style.overflowX + style.overflowY)) {
        const clip = node.getBoundingClientRect();
        if (rect.bottom <= clip.top || rect.top >= clip.bottom || rect.right <= clip.left || rect.left >= clip.right) return false;
      }
    }
    return true;
  }
  function bounds(el) {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)};
  }
  function hittable(el) {
    const rect = el.getBoundingClientRect();
    const left = Math.max(0, rect.left), right = Math.min(global.innerWidth, rect.right);
    const top = Math.max(0, rect.top), bottom = Math.min(global.innerHeight, rect.bottom);
    if (right <= left || bottom <= top) return false;
    const points = [[0.5, 0.5], [0.15, 0.15], [0.85, 0.15], [0.15, 0.85], [0.85, 0.85]];
    return points.some(([x, y]) => {
      const hit = global.document.elementFromPoint(left + (right - left) * x, top + (bottom - top) * y);
      return hit === el || (hit && el.contains(hit));
    });
  }
  function label(el) {
    if (sensitive(el)) return 'Protected settings';
    const explicit = attr(el, 'aria-label') || attr(el, 'title');
    if (explicit) return norm(explicit, 300);
    if (el.labels && el.labels.length) return norm(el.labels[0].textContent, 300);
    return norm(el.textContent || attr(el, 'placeholder') || el.id || el.tagName.toLowerCase(), 300);
  }
  function role(el) {
    return norm(attr(el, 'role') || ({BUTTON: 'button', TEXTAREA: 'textbox', INPUT: el.type === 'checkbox' ? 'checkbox' : 'textbox', SELECT: 'combobox', A: 'link'}[el.tagName]) || 'button', 40);
  }
  function disabled(el) { return !!el.disabled || !!el.closest('[inert]') || attr(el, 'aria-disabled') === 'true'; }
  function signature(el) {
    return JSON.stringify([el.id, el.tagName, attr(el, 'onclick'), el.value, el.checked, disabled(el), label(el), bounds(el),
      attr(el, 'data-studio-frame'), attr(el, 'data-studio-frame-id'), attr(el, 'data-studio-action'),
      attr(el, 'data-artist'), el.scrollTop, el.scrollLeft]);
  }
  function scrollable(el) {
    if (sensitive(el) || !visible(el)) return false;
    const style = global.getComputedStyle(el);
    return /(auto|scroll)/.test(style.overflow + style.overflowX + style.overflowY) &&
      (el.scrollHeight > el.clientHeight + 4 || el.scrollWidth > el.clientWidth + 4);
  }
  function protectedScreen(doc) {
    return Array.from(doc.querySelectorAll(SENSITIVE_ROOTS)).some(visible);
  }
  function validateValue(el, value) {
    if (typeof value !== 'string' || value.length > 16000 || value.includes('\u0000')) throw error('invalid_value');
    if (el.maxLength >= 0 && value.length > el.maxLength) throw error('invalid_value');
    if (el.type === 'number' || el.type === 'range') {
      const n = Number(value);
      if (!value.trim() || !Number.isFinite(n) || (el.min !== '' && n < Number(el.min)) || (el.max !== '' && n > Number(el.max))) throw error('invalid_value');
    }
    return value;
  }
  function setValue(el, value) {
    const proto = el.tagName === 'TEXTAREA' ? global.HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? global.HTMLSelectElement.prototype : global.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new global.Event('input', {bubbles: true}));
    // A studio input handler may re-render, replacing the original element. Do
    // not dispatch a second event through a detached element.
    if (el.isConnected) el.dispatchEvent(new global.Event('change', {bubbles: true}));
  }
  async function capture(doc) {
    if (typeof global.html2canvas !== 'function') throw error('capture_unavailable');
    if (protectedScreen(doc)) throw error('protected_screen');
    let canvas;
    try {
      const scale = Math.min(1, 1600 / Math.max(global.innerWidth, global.innerHeight));
      canvas = await global.html2canvas(doc.body, {
        width: global.innerWidth, height: global.innerHeight, x: global.scrollX, y: global.scrollY,
        windowWidth: global.innerWidth, windowHeight: global.innerHeight, scale,
        logging: false, useCORS: false, allowTaint: false, backgroundColor: '#15171d',
        imageTimeout: 3000,
        ignoreElements: el => el.tagName === 'IFRAME' || el.tagName === 'SCRIPT' || sensitive(el),
        onclone: clone => {
          for (const node of clone.querySelectorAll('iframe,script,' + SENSITIVE_ROOTS + ',' + SECRET_FIELDS)) node.remove();
          for (const node of clone.querySelectorAll('input,textarea,select')) {
            if (sensitive(node)) { node.value = ''; node.textContent = ''; node.style.visibility = 'hidden'; }
          }
        },
      });
      if (protectedScreen(doc)) throw error('protected_screen');
      for (let attempt = 0; attempt < 5; attempt++) {
        const data = canvas.toDataURL('image/jpeg', attempt ? 0.5 : 0.72).split(',')[1];
        if (data && data.length <= 600000) return {mimeType: 'image/jpeg', data, width: canvas.width, height: canvas.height};
        const reduced = doc.createElement('canvas');
        reduced.width = Math.max(1, Math.floor(canvas.width * 0.72));
        reduced.height = Math.max(1, Math.floor(canvas.height * 0.72));
        reduced.getContext('2d').drawImage(canvas, 0, 0, reduced.width, reduced.height);
        canvas.width = canvas.height = 0;
        canvas = reduced;
      }
      throw error('capture_too_large');
    } finally { if (canvas) canvas.width = canvas.height = 0; }
  }
  function install({frame, panelOrigin}) {
    const doc = global.document;
    if (!frame || frame.tagName !== 'IFRAME' || typeof panelOrigin !== 'string' || new URL(panelOrigin).origin !== panelOrigin) throw error('invalid_bridge');
    if (global.__naiaScreenAgentDispose) global.__naiaScreenAgentDispose();
    const snapshots = new Map();
    const now = () => global.performance?.now ? global.performance.now() : Date.now();
    const pruneSnapshots = () => {
      const time = now();
      for (const [id, saved] of snapshots) if (time - saved.created >= SNAPSHOT_TTL_MS) snapshots.delete(id);
      while (snapshots.size > MAX_SNAPSHOTS) snapshots.delete(snapshots.keys().next().value);
    };
    let epoch = 0, disposed = false, pending = Promise.resolve();
    // customSelects refreshes identical attributes/labels every 750ms. Ignore
    // no-op mutations, otherwise no snapshot would survive a round trip. A
    // replaced target is independently rejected by its identity/isConnected.
    const relevant = record => {
      const host = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      // These audited read-only status counters do not change action semantics.
      if (host && host.closest('#__gpuprobe,#poolLoadToast')) return false;
      if (record.type === 'childList' && [...record.addedNodes, ...record.removedNodes].every(node => node.nodeType === 1 && node.classList.contains('html2canvas-container'))) return false;
      if (record.type === 'attributes') return record.oldValue !== record.target.getAttribute(record.attributeName);
      if (record.type === 'characterData') return record.oldValue !== record.target.data;
      const describe = nodes => Array.from(nodes, node => node.outerHTML || node.textContent || '').join('');
      return describe(record.addedNodes) !== describe(record.removedNodes);
    };
    function observeChanges(records) {
      pruneSnapshots();
      for (const record of records) {
        if (!relevant(record)) continue;
        epoch++; // Captures still require a stable whole screen.
        const host = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        for (const saved of snapshots.values()) for (const entry of saved.entries.values()) {
          // Background counters, thumbnail loads and unrelated panels must not
          // expire a stable Quick/menu button. Changes to that exact control,
          // its contents, or ancestor attributes still invalidate that target.
          if (host && [entry.el, entry.source].some(node =>
              node === host || node.contains(host) ||
              (record.type === 'attributes' && host.contains(node)))) entry.dirty = true;
        }
      }
    }
    const observer = new global.MutationObserver(observeChanges);
    observer.observe(doc.body, {subtree: true, childList: true, characterData: true, characterDataOldValue: true, attributes: true, attributeOldValue: true});
    const flush = () => observeChanges(observer.takeRecords());
    const changed = event => {
      if (!event.isTrusted) return;
      epoch++;
      pruneSnapshots();
      for (const saved of snapshots.values()) saved.userChanged = true;
    };
    // A browser-generated scroll/resize is also isTrusted (scroll anchoring,
    // WebView viewport updates). It is not proof of a new user action. Keep
    // the token and recheck exact target identity, bounds, value and overlays
    // when executing. Real input blocks edits and page-wide scrolling, while
    // an unchanged, audited navigation target can still be used.
    for (const name of ['pointerdown', 'keydown']) doc.addEventListener(name, changed, true);
    const layoutChanged = () => { epoch++; };
    doc.addEventListener('scroll', layoutChanged, true);
    global.addEventListener('resize', layoutChanged);
    function snapshot(permissions) {
      if (protectedScreen(doc)) throw error('protected_screen');
      flush();
      const entries = new Map(), controls = [];
      let remaining = 30000;
      const add = (el, scrolling = false) => {
        if (controls.length >= MAX_CONTROLS || !visible(el)) return;
        const source = scrolling ? el : sourceElement(el);
        const p = scrolling ? {actions: ['scroll'], edit: false} : policy(source);
        const covered = !hittable(el);
        const can = !disabled(source) && !covered && permissions.control === true && (!p.edit || permissions.edit === true);
        const target = 't' + (controls.length + 1);
        const item = {target, role: scrolling ? 'scrollarea' : role(source), label: label(source), enabled: !disabled(source), actions: can ? p.actions : [], bounds: bounds(el)};
        if (p.reason || !can) item.blocked_reason = p.reason || (disabled(source) ? 'disabled' : covered ? 'covered_control' : p.edit && !permissions.edit ? 'edit_permission_required' : 'control_permission_required');
        if (!scrolling && !sensitive(source) && ['INPUT', 'TEXTAREA', 'SELECT'].includes(source.tagName)) {
          item.value = String(source.type === 'checkbox' || source.type === 'radio' ? !!source.checked : source.value || '').slice(0, Math.min(4000, remaining));
          remaining -= item.value.length;
          if (source.tagName === 'SELECT') item.options = Array.from(source.options).filter(o => optionAllowed(source, o)).slice(0, 160).map(o => ({value: String(o.value).slice(0, 4000), label: norm(o.label, 300)}));
        }
        controls.push(item); entries.set(target, {el, source, signature: signature(el), sourceSignature: signature(source), scrolling});
      };
      // Navigation and fields first, so a large thumbnail grid cannot starve
      // toolbar and editor controls from the bounded snapshot.
      const elements = Array.from(doc.querySelectorAll(INTERACTIVE)).filter(visible);
      elements.sort((a, b) => Number(!a.id) - Number(!b.id));
      for (const el of elements.slice(0, 140)) add(el);
      for (const el of doc.querySelectorAll('main,aside,section,div,textarea')) {
        if (controls.length >= MAX_CONTROLS) break;
        if (scrollable(el)) add(el, true);
      }
      let text = '';
      const walker = doc.createTreeWalker(doc.body, 4);
      while (walker.nextNode() && text.length < 12000) {
        const node = walker.currentNode, el = node.parentElement;
        if (!el || el.closest('script,style,iframe,textarea,select') || sensitive(el) || !visible(el)) continue;
        const part = norm(node.textContent, 500);
        if (part) text += (text ? '\n' : '') + part;
      }
      const random = global.crypto.randomUUID ? global.crypto.randomUUID() : Array.from(global.crypto.getRandomValues(new Uint32Array(4)), n => n.toString(36)).join('_');
      const result = {agent_version: '0.2.4', snapshot_id: 'screen_' + random, title: 'NAIA', viewport: {width: global.innerWidth, height: global.innerHeight, scroll_x: global.scrollX, scroll_y: global.scrollY}, controls, text: text.slice(0, 12000), blocked: false};
      // Option dictionaries can be unusually large. Truncate options before
      // dropping controls, preserving all actionable target mappings.
      while (new TextEncoder().encode(JSON.stringify(result)).length > 260000) {
        const largest = controls.filter(c => c.options && c.options.length).sort((a, b) => b.options.length - a.options.length)[0];
        if (!largest) throw error('screen_too_large');
        largest.options.splice(Math.max(0, Math.floor(largest.options.length / 2)));
      }
      snapshots.set(result.snapshot_id, {created: now(), entries, userChanged: false});
      pruneSnapshots();
      return result;
    }
    async function run(command) {
      const permissions = command.permissions || {}, args = command.arguments || {};
      if (permissions.screen !== true) throw error('screen_permission_required');
      if (protectedScreen(doc)) throw error('protected_screen');
      if (command.name === 'naia_get_screen') {
        if (args.capture !== undefined && typeof args.capture !== 'boolean') throw error('invalid_arguments');
        flush();
        const before = epoch;
        const image = args.capture === true ? await capture(doc) : null;
        flush();
        if (image && before !== epoch) throw error('screen_changed_during_capture');
        const result = snapshot(permissions);
        if (image) result.image = image;
        return result;
      }
      if (command.name !== 'naia_ui_action') throw error('unknown_command');
      if (permissions.control !== true) throw error('control_permission_required');
      flush();
      const saved = typeof args.snapshot_id === 'string' ? snapshots.get(args.snapshot_id) : null;
      // Reads may overlap, but every attempted action consumes every old token.
      // This prevents another saved view from replaying the same action.
      snapshots.clear();
      if (!saved) throw error('stale_snapshot');
      const entry = saved.entries.get(args.target), action = args.action;
      if (!['click', 'fill', 'select', 'scroll'].includes(action)) throw error('invalid_action');
      if (action === 'scroll' && args.target === undefined) {
        if (saved.userChanged) throw error('stale_user_input');
        if (!['up', 'down', 'left', 'right'].includes(args.direction)) throw error('invalid_direction');
        const amount = args.amount === undefined ? 500 : args.amount;
        if (!Number.isInteger(amount) || amount < 1 || amount > 2000) throw error('invalid_amount');
        global.scrollBy({top: args.direction === 'down' ? amount : args.direction === 'up' ? -amount : 0, left: args.direction === 'right' ? amount : args.direction === 'left' ? -amount : 0, behavior: 'instant'});
      } else {
        if (!entry || entry.dirty || !visible(entry.el) || !entry.source.isConnected || signature(entry.el) !== entry.signature || signature(entry.source) !== entry.sourceSignature || (!entry.scrolling && sourceElement(entry.el) !== entry.source)) throw error('stale_target');
        if (!hittable(entry.el)) throw error('covered_control');
        const el = entry.source, p = entry.scrolling ? {actions: scrollable(el) ? ['scroll'] : [], edit: false} : policy(el);
        if (disabled(el) || !p.actions.includes(action)) throw error(p.reason || 'action_not_allowed');
        if (saved.userChanged && !(p.edit === false && (action === 'click' || (entry.scrolling && action === 'scroll')))) throw error('stale_user_input');
        if (p.edit && permissions.edit !== true) throw error('edit_permission_required');
        if (action === 'click') {
          // Reject ancestors whose actual clickable child has another action.
          // The command clicks exactly the audited element, never coordinates.
          el.click();
        } else if (action === 'fill') setValue(el, validateValue(el, args.value));
        else if (action === 'select') {
          if (typeof args.value !== 'string' || args.value.length > 16000 || !Array.from(el.options).some(o => o.value === args.value && optionAllowed(el, o))) throw error('invalid_option');
          setValue(el, args.value);
        } else {
          if (!['up', 'down', 'left', 'right'].includes(args.direction)) throw error('invalid_direction');
          const amount = args.amount === undefined ? 500 : args.amount;
          if (!Number.isInteger(amount) || amount < 1 || amount > 2000) throw error('invalid_amount');
          el.scrollBy({top: args.direction === 'down' ? amount : args.direction === 'up' ? -amount : 0, left: args.direction === 'right' ? amount : args.direction === 'left' ? -amount : 0, behavior: 'instant'});
        }
      }
      await new Promise(resolve => global.setTimeout(resolve, 180));
      return snapshot(permissions);
    }
    const receive = event => {
      if (disposed || event.source !== frame.contentWindow || event.origin !== panelOrigin) return;
      const command = event.data;
      if (command && command.type === 'naia-android-ready') {
        frame.contentWindow.postMessage({type: 'naia-ui-ready'}, panelOrigin);
        return;
      }
      if (!command || command.type !== 'naia-ui-command' || typeof command.id !== 'string' || command.id.length > 120) return;
      pending = pending.then(async () => {
        if (disposed) return;
        let result, is_error = false;
        try { result = await run(command); }
        catch (e) {
          if (command.name === 'naia_ui_action' || e.code === 'protected_screen') snapshots.clear();
          result = {error: {code: typeof e.code === 'string' ? e.code : 'screen_action_failed'}}; is_error = true;
        }
        if (!disposed) frame.contentWindow.postMessage({type: 'naia-ui-result', id: command.id, result, is_error}, panelOrigin);
      });
    };
    global.addEventListener('message', receive);
    frame.contentWindow.postMessage({type: 'naia-ui-ready'}, panelOrigin);
    global.__naiaScreenAgentDispose = () => {
      disposed = true; snapshots.clear(); observer.disconnect();
      global.removeEventListener('message', receive); global.removeEventListener('resize', layoutChanged);
      for (const name of ['pointerdown', 'keydown']) doc.removeEventListener(name, changed, true);
      doc.removeEventListener('scroll', layoutChanged, true);
    };
  }
  global.__naiaInstallScreenAgent = install;
  // Pure policy exports are only available in Node tests, never over the bridge.
  if (typeof module === 'object' && module.exports) module.exports = {policy, optionAllowed, validateValue, normalizeHandler};
})(typeof window === 'object' ? window : globalThis);
