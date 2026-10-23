/* NAIA Android: persistent, in-app photo library. No native picker dependency. */
(function (global) {
  'use strict';

  function count(value) { return Math.max(0, Math.floor(Number(value) || 0)); }
  function errorMessage(error, fallback = '불러오지 못했습니다. 다시 시도해 주세요.') {
    const value = typeof error === 'string' ? error : error?.message;
    return typeof value === 'string' && value.trim() ? value.slice(0, 1000) : fallback;
  }
  function jobSummary(job) {
    if (!job) return '';
    const state = {running: '불러오는 중', completed: '불러오기 완료', cancelled: '불러오기 취소됨', error: '불러오기 중단됨'}[job.state] || '불러오기';
    const total = job.total == null ? '' : ` / ${count(job.total)}`;
    let text = `${state} · 확인 ${count(job.processed)}${total}장 · 추가 ${count(job.imported)} · 중복 ${count(job.duplicates)} · 실패 ${count(job.failed)}`;
    if (count(job.skipped)) text += ` · 건너뜀 ${count(job.skipped)}`;
    if (job.error) text += ` · ${errorMessage(job.error, '일부 사진을 불러오지 못했습니다.')}`;
    return text;
  }
  function metadataText(value, limit = 20000) {
    let result = '';
    if (typeof value === 'string') result = value;
    else if (value && typeof value === 'object') {
      const sections = [], settings = [], parameters = value.parameters || {};
      if (typeof value.prompt === 'string' && value.prompt.trim()) sections.push('프롬프트\n' + value.prompt);
      if (typeof value.negative === 'string' && value.negative.trim()) sections.push('네거티브 프롬프트\n' + value.negative);
      if (count(value.width) && count(value.height)) settings.push(`크기: ${count(value.width)} × ${count(value.height)}`);
      for (const [key, label] of [['model', '모델'], ['seed', '시드'], ['steps', '스텝'], ['sampler', '샘플러']]) {
        const item = parameters[key];
        if ((typeof item === 'string' && item) || typeof item === 'number') settings.push(`${label}: ${item}`);
      }
      const scale = parameters.scale ?? parameters.cfg_scale;
      if ((typeof scale === 'string' && scale) || typeof scale === 'number') settings.push(`프롬프트 강도: ${scale}`);
      if (settings.length) sections.push('생성 설정\n' + settings.join('\n'));
      result = sections.join('\n\n');
    }
    if (!result) return '저장된 프롬프트·설정 정보가 없습니다.';
    return result.length > limit ? result.slice(0, limit) + '\n… 긴 정보의 일부만 표시합니다.' : result;
  }
  function selectForComparison(selected, item, checked) {
    const id = String(item.id);
    if (!checked) { selected.delete(id); return true; }
    if (!selected.has(id) && selected.size >= 2) return false;
    selected.set(id, item); return true;
  }
  function createTaskQueue(limit = 2) {
    const parallel = Math.max(1, Math.min(2, count(limit)));
    let active = 0, closed = false;
    const waiting = [];
    function pump() {
      while (!closed && active < parallel && waiting.length) {
        const job = waiting.shift(); active++;
        Promise.resolve().then(job.task).then(job.resolve, job.reject).finally(() => { active--; pump(); });
      }
    }
    function clear() { for (const job of waiting.splice(0)) job.resolve(undefined); }
    return {
      run(task) { if (closed) return Promise.resolve(undefined); return new Promise((resolve, reject) => { waiting.push({task, resolve, reject}); pump(); }); },
      clear,
      close() { closed = true; clear(); },
    };
  }
  function isHistoryOpener(element) {
    const code = element?.getAttribute?.('onclick') || '';
    return /^\s*(?:return\s+)?openResultFolder\(\s*\)\s*;?\s*$/.test(code);
  }

  // Tests can import these without a DOM; no browser debug API is exposed.
  if (typeof module === 'object' && module.exports) {
    module.exports = {count, jobSummary, metadataText, selectForComparison, createTaskQueue, isHistoryOpener};
  }

  global.__naiaInstallHistoryLibrary = function ({request, asset}) {
    if (typeof request !== 'function' || typeof asset !== 'function') throw new TypeError('사진 보관함 연결이 필요합니다.');
    global.__naiaHistoryLibraryController?.destroy();
    const document = global.document;
    const queue = createTaskQueue(2);
    const selected = new Map(), changedOpeners = new Map();
    let dialog = null, content = null, toolbar = null, status = null, progress = null, progressText = null, cancelButton = null;
    let viewVersion = 0, session = 0, roots = [], job = null, page = 1, mode = 'gallery';
    let pollTimer = null, polling = false, importing = false, cancelling = false, destroyed = false;
    let lazyObserver = null, returnFocus = null, updateBrowseActions = null;
    const objectURLs = new Set();
    const style = document.createElement('style');
    style.textContent = `
      .naia-hl{box-sizing:border-box;width:min(1000px,96vw);height:min(900px,94dvh);max-width:98vw;max-height:96dvh;margin:auto;padding:0;border:1px solid #586575;border-radius:16px;background:#141923;color:#edf1f7;font:15px/1.45 system-ui,sans-serif;box-shadow:0 20px 90px #0009;overflow:hidden}
      .naia-hl::backdrop{background:#080c16bb}.naia-hl[open]{display:flex;flex-direction:column}.naia-hl.is-compare{width:98vw;height:96dvh}
      .naia-hl *{box-sizing:border-box}.naia-hl [hidden]{display:none!important}.naia-hl button,.naia-hl input{font:inherit}.naia-hl button{min-height:44px;padding:9px 14px;color:inherit;background:#283243;border:1px solid #566379;border-radius:9px;cursor:pointer;touch-action:manipulation}.naia-hl button:disabled{opacity:.45;cursor:default}.naia-hl button:focus-visible,.naia-hl input:focus-visible{outline:3px solid #a9c7ff;outline-offset:2px}.naia-hl button.naia-hl-primary{background:#41629c;border-color:#779ad3}.naia-hl h2{font-size:19px;margin:0}.naia-hl p{margin:0}.naia-hl-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px 8px}.naia-hl-note{font-size:12px;color:#b4c0d2;padding:0 16px 10px}.naia-hl-toolbar{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px 10px}.naia-hl-status{min-height:1.4em;padding:0 16px 9px;font-size:13px;overflow-wrap:anywhere}.naia-hl-status.is-error{color:#ffb5b5}.naia-hl-progress{margin:0 16px 10px;padding:10px;border:1px solid #566379;border-radius:9px;display:flex;align-items:center;gap:12px;font-size:13px}.naia-hl-progress span{flex:1;overflow-wrap:anywhere}.naia-hl-content{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:4px 16px 16px}.naia-hl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}.naia-hl-card{min-width:0;border:1px solid #40506a;border-radius:10px;overflow:hidden;background:#1c2330}.naia-hl-card.is-selected{border-color:#a9c7ff;box-shadow:0 0 0 1px #a9c7ff}.naia-hl button.naia-hl-thumb{display:block;width:100%;height:148px;border:0;border-radius:0;padding:4px;background:#10151e}.naia-hl-thumb img{width:100%;height:100%;object-fit:contain}.naia-hl-check{display:flex;align-items:center;gap:8px;min-height:44px;padding:8px;cursor:pointer;overflow-wrap:anywhere;font-size:12px}.naia-hl-check input{flex:none;width:22px;height:22px;accent-color:#9abaff}.naia-hl-check span{min-width:0;flex:1}.naia-hl-nav{display:flex;align-items:center;justify-content:center;gap:14px;margin:18px 0 0}.naia-hl-empty{padding:32px 12px;text-align:center;color:#c0cad9;white-space:pre-line}.naia-hl-roots{display:grid;gap:10px}.naia-hl-root{display:flex;justify-content:space-between;align-items:center;gap:12px}.naia-hl-root small{color:#c4ccda}.naia-hl-path{overflow-wrap:anywhere;margin:4px 0 12px!important;color:#c4ccda;font-size:13px}.naia-hl-import-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0}.naia-hl-folders{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin:8px 0 14px}.naia-hl-folders button{text-align:left;overflow-wrap:anywhere}.naia-hl-files{display:grid;gap:5px}.naia-hl-files label{font-size:14px;background:#1c2330;border:1px solid #40506a;border-radius:9px}.naia-hl-files small{display:block;color:#aebbd0}.naia-hl-preview-grid{display:grid;grid-template-columns:1fr;gap:14px}.naia-hl-preview-grid.is-pair{grid-template-columns:1fr 1fr}.naia-hl-preview{min-width:0;margin:0}.naia-hl-preview-media{height:min(58dvh,620px);display:flex;align-items:center;justify-content:center;background:#0b1018;border-radius:10px;overflow:hidden}.naia-hl-preview-media img{width:100%;height:100%;object-fit:contain}.naia-hl-preview figcaption{overflow-wrap:anywhere;margin:8px 0;color:#c8d3e5}.naia-hl-preview details{border:1px solid #40506a;border-radius:9px;padding:10px}.naia-hl-preview summary{min-height:30px;cursor:pointer}.naia-hl-preview pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.55 ui-monospace,monospace;max-height:30dvh;overflow:auto}.naia-hl-inline-error{padding:12px;color:#ffb5b5;overflow-wrap:anywhere;font-size:13px}.naia-hl-import-actions .naia-hl-check{padding-left:0}.naia-hl-subnote{color:#b4c0d2;font-size:12px;margin:6px 0 12px!important}
      .naia-hl-sticky-actions{position:sticky;top:0;z-index:1;background:#141923;padding:10px 0;border-bottom:1px solid #40506a}
      @media(max-width:560px){.naia-hl{width:98vw;height:96dvh;border-radius:12px}.naia-hl-head{padding:10px 10px 8px}.naia-hl-toolbar{padding:0 10px 8px;gap:6px}.naia-hl-content{padding:4px 10px 12px}.naia-hl-note,.naia-hl-status{padding-left:10px;padding-right:10px}.naia-hl-progress{margin-left:10px;margin-right:10px}.naia-hl-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.naia-hl button{padding:9px 10px}.naia-hl-preview-grid.is-pair{gap:7px}.naia-hl-preview-grid.is-pair .naia-hl-preview-media{height:52dvh}.naia-hl-preview-grid.is-pair figcaption{font-size:12px}.naia-hl-preview details{padding:7px}.naia-hl-preview summary{font-size:12px}.naia-hl-progress{align-items:flex-start}.naia-hl-folders{grid-template-columns:1fr}}
    `;
    (document.head || document.body).append(style);

    function element(tag, text, className) {
      const value = document.createElement(tag);
      if (text != null) value.textContent = String(text);
      if (className) value.className = className;
      return value;
    }
    function button(text, action, primary = false) {
      const value = element('button', text, primary ? 'naia-hl-primary' : ''); value.type = 'button';
      value.addEventListener('click', action); return value;
    }
    function notify(text, error = false) {
      if (!status) return;
      status.textContent = text; status.classList.toggle('is-error', error);
    }
    async function call(action, data = {}) {
      const value = await request(action, data);
      if (!value || typeof value !== 'object' || value.ok === false || value.error) {
        throw new Error(errorMessage(value?.error, '사진 보관함에 연결하지 못했습니다.'));
      }
      return value;
    }
    function clearView() {
      viewVersion++; lazyObserver?.disconnect(); lazyObserver = null; updateBrowseActions = null; queue.clear();
      for (const url of objectURLs) global.URL.revokeObjectURL(url);
      objectURLs.clear(); content?.replaceChildren(); toolbar?.replaceChildren();
      dialog?.classList.remove('is-compare');
      return viewVersion;
    }
    function current(version) { return !!dialog && !destroyed && version === viewVersion; }
    function running() { return job?.state === 'running'; }
    function drawJob() {
      if (!progress) return;
      updateBrowseActions?.();
      progress.hidden = !job;
      progressText.textContent = jobSummary(job) + (cancelling && running() ? ' · 취소 요청 중…' : '');
      cancelButton.hidden = !running(); cancelButton.disabled = cancelling;
    }
    function schedulePoll() {
      if (!dialog || destroyed || !running() || pollTimer || polling) return;
      pollTimer = global.setTimeout(pollJob, 1000);
    }
    function setJob(value) { job = value || null; if (!running()) { cancelling = false; global.clearTimeout(pollTimer); pollTimer = null; } drawJob(); schedulePoll(); }
    async function pollJob() {
      pollTimer = null;
      if (!dialog || !running() || polling || destroyed) return;
      const sid = session; polling = true;
      try {
        const response = await call('job', {id: job.id});
        if (sid !== session || !dialog) return;
        const wasRunning = running(); setJob(response.job);
        if (wasRunning && !running()) {
          notify(jobSummary(job), job?.state === 'error' || count(job?.failed) > 0);
          if (mode === 'gallery') void loadGallery(1);
        }
      } catch (error) { if (sid === session && dialog) notify(errorMessage(error, '진행 상황을 확인하지 못했습니다.'), true); }
      finally { polling = false; schedulePoll(); }
    }
    async function cancelImport() {
      if (!running() || cancelling) return;
      cancelling = true; drawJob(); const sid = session;
      try {
        const response = await call('cancel', {id: job.id});
        if (sid !== session || !dialog) return;
        setJob(response.job);
        if (!running() && mode === 'gallery') void loadGallery(1);
      } catch (error) { if (sid === session && dialog) { cancelling = false; drawJob(); notify(errorMessage(error), true); } }
    }
    function navigation(targetPage, pages, action) {
      const nav = element('div', null, 'naia-hl-nav');
      const previous = button('이전', () => action(targetPage - 1)), next = button('다음', () => action(targetPage + 1));
      previous.disabled = targetPage <= 1; next.disabled = targetPage >= pages;
      const position = element('span', `${targetPage} / ${pages}`); position.setAttribute('aria-label', `페이지 ${targetPage} / ${pages}`);
      nav.append(previous, position, next); return nav;
    }
    async function showAsset(img, id, kind, version, errorContainer) {
      try {
        await queue.run(async () => {
          if (!current(version)) return;
          const blob = await asset(id, kind);
          if (!current(version)) return;
          if (!blob || typeof blob.arrayBuffer !== 'function') throw new Error('사진 데이터를 받지 못했습니다.');
          const url = global.URL.createObjectURL(blob); objectURLs.add(url);
          img.addEventListener('error', () => {
            if (current(version)) { img.hidden = true; errorContainer.append(element('span', '사진을 표시하지 못했습니다.', 'naia-hl-inline-error')); }
          }, {once: true});
          img.src = url;
        });
      } catch (error) {
        if (current(version)) { img.hidden = true; errorContainer.append(element('span', errorMessage(error, '사진을 불러오지 못했습니다.'), 'naia-hl-inline-error')); }
      }
    }
    function galleryToolbar() {
      toolbar.append(button('사진 불러오기', showRoots, true), button('새로고침', refresh));
      const compare = button(`선택한 사진 비교 (${selected.size}/2)`, () => showDetail([...selected.values()]));
      compare.disabled = selected.size !== 2;
      const clear = button('선택 해제', () => { selected.clear(); void loadGallery(page); }); clear.disabled = !selected.size;
      toolbar.append(compare, clear);
      return () => { compare.textContent = `선택한 사진 비교 (${selected.size}/2)`; compare.disabled = selected.size !== 2; clear.disabled = !selected.size; };
    }
    async function loadGallery(wanted = 1) {
      if (!dialog) return;
      mode = 'gallery'; const version = clearView();
      const updateSelection = galleryToolbar(); content.append(element('p', '사진 목록을 불러오는 중…', 'naia-hl-empty'));
      try {
        const data = await call('list', {page: Math.max(1, count(wanted)), per_page: 30});
        if (!current(version)) return;
        page = Math.max(1, count(data.page) || 1); const pages = Math.max(1, count(data.pages));
        if (page > pages) { void loadGallery(pages); return; }
        content.replaceChildren();
        content.append(element('p', `보관함 ${count(data.total)}장 · 사진을 누르면 크게 볼 수 있습니다.`, 'naia-hl-path'));
        const grid = element('div', null, 'naia-hl-grid'); content.append(grid);
        const loaders = new Map();
        if (typeof global.IntersectionObserver === 'function') {
          lazyObserver = new global.IntersectionObserver(entries => {
            for (const entry of entries) if (entry.isIntersecting) { lazyObserver?.unobserve(entry.target); loaders.get(entry.target)?.(); loaders.delete(entry.target); }
          }, {root: content, rootMargin: '160px'});
        }
        for (const item of (Array.isArray(data.items) ? data.items : []).slice(0, 30)) {
          if (item?.id == null) continue;
          const card = element('div', null, 'naia-hl-card');
          const thumb = button('', () => showDetail([item])); thumb.className = 'naia-hl-thumb'; thumb.setAttribute('aria-label', `${item.name || '사진'} 크게 보기`);
          const img = element('img'); img.alt = String(item.name || '사진'); img.loading = 'lazy'; img.decoding = 'async'; thumb.append(img);
          const label = element('label', null, 'naia-hl-check'), check = element('input'); check.type = 'checkbox';
          check.checked = selected.has(String(item.id)); check.setAttribute('aria-label', `${item.name || '사진'} 비교 선택`);
          card.classList.toggle('is-selected', check.checked);
          check.addEventListener('change', () => {
            if (!selectForComparison(selected, item, check.checked)) { check.checked = false; notify('비교할 사진은 두 장까지 선택해 주세요.'); }
            card.classList.toggle('is-selected', check.checked); updateSelection();
          });
          label.append(check, element('span', item.name || '사진')); card.append(thumb, label); grid.append(card);
          const load = () => { void showAsset(img, item.id, 'thumb', version, thumb); };
          if (lazyObserver) { loaders.set(thumb, load); lazyObserver.observe(thumb); } else load();
        }
        if (!grid.childElementCount) content.append(element('p', '아직 보관함에 사진이 없습니다.\n위의 ‘사진 불러오기’에서 파일이나 폴더를 선택하세요.', 'naia-hl-empty'));
        content.append(navigation(page, pages, loadGallery));
      } catch (error) { if (current(version)) { content.replaceChildren(element('p', errorMessage(error), 'naia-hl-inline-error'), button('다시 불러오기', () => loadGallery(wanted))); } }
    }
    async function refresh() {
      const sid = session; notify('보관함을 새로 확인하는 중…');
      try { const data = await call('state'); if (sid !== session || !dialog) return; roots = Array.isArray(data.roots) ? data.roots : []; setJob(data.job); notify(''); await loadGallery(page); }
      catch (error) { if (sid === session && dialog) notify(errorMessage(error), true); }
    }
    function showRoots() {
      if (!dialog) return;
      mode = 'browse'; clearView();
      toolbar.append(button('보관함으로', () => loadGallery(page)));
      content.append(element('h2', '사진이 있는 폴더 선택'), element('p', '폴더를 열어 사진을 고르거나 폴더 전체를 불러오세요.', 'naia-hl-path'));
      const list = element('div', null, 'naia-hl-roots'); content.append(list);
      for (const root of roots) {
        const target = button('', () => browse(root, '', 1)); target.className = 'naia-hl-root';
        target.append(element('span', root.label || '폴더'), element('small', root.available === false ? '현재 앱에서 접근할 수 없음' : '열기'));
        target.disabled = root.available === false; list.append(target);
      }
      if (!roots.some(root => root.available !== false)) content.append(element('p', '앱에서 읽을 수 있는 폴더가 없습니다. 접근 가능한 저장 폴더가 있는지 확인해 주세요.', 'naia-hl-empty'));
    }
    async function startImport(root, relative, options) {
      if (importing || running()) { notify('현재 불러오기가 끝나면 다음 사진을 선택할 수 있습니다.'); return; }
      importing = true; drawJob(); const sid = session; notify('선택한 사진을 불러오는 중…');
      try {
        const response = await call('import', {root_id: root.id, relative, ...options});
        if (sid !== session || !dialog) return;
        setJob(response.job); notify(jobSummary(job)); await loadGallery(1);
      } catch (error) { if (sid === session && dialog) notify(errorMessage(error), true); }
      finally { importing = false; drawJob(); }
    }
    async function browse(root, relative, wanted = 1, picked = new Set(), recursive = false) {
      if (!dialog) return;
      mode = 'browse'; const version = clearView();
      toolbar.append(button('보관함으로', () => loadGallery(page)), button('폴더 목록', showRoots));
      content.append(element('p', '폴더를 불러오는 중…', 'naia-hl-empty'));
      try {
        const data = await call('browse', {root_id: root.id, relative, page: wanted});
        if (!current(version)) return;
        const actual = typeof data.relative === 'string' ? data.relative : relative;
        content.replaceChildren(); content.append(element('h2', root.label || '폴더'), element('p', actual ? `${root.label || '폴더'} / ${actual}` : '폴더의 첫 화면', 'naia-hl-path'));
        const up = button('위로', () => data.parent == null ? showRoots() : browse(root, data.parent, 1)); content.append(up);
        const actions = element('div', null, 'naia-hl-import-actions naia-hl-sticky-actions');
        const selectedButton = button(`선택한 사진 불러오기 (${picked.size})`, () => startImport(root, actual, {files: [...picked]}), true);
        const folderButton = button('이 폴더 전체', () => startImport(root, actual, {whole_folder: true, recursive: subfolders.checked}));
        const subLabel = element('label', null, 'naia-hl-check'), subfolders = element('input'); subfolders.type = 'checkbox'; subfolders.checked = recursive;
        subLabel.append(subfolders, element('span', '하위 폴더 포함'));
        const update = () => { selectedButton.textContent = `선택한 사진 불러오기 (${picked.size})`; selectedButton.disabled = !picked.size || importing || running(); folderButton.disabled = importing || running(); };
        updateBrowseActions = update;
        actions.append(selectedButton, folderButton, subLabel); content.append(actions);
        content.append(element('p', '선택한 사진은 보관함에 복사됩니다. 원본 파일은 그대로 유지됩니다.', 'naia-hl-subnote'));
        if (running()) content.append(element('p', '불러오기가 진행 중입니다. 위의 진행 상황에서 취소할 수 있습니다.', 'naia-hl-subnote'));
        const folders = element('div', null, 'naia-hl-folders');
        for (const folder of Array.isArray(data.dirs) ? data.dirs : []) {
          if (typeof folder?.relative !== 'string') continue;
          folders.append(button(`📁 ${folder.name || '폴더'}`, () => browse(root, folder.relative, 1)));
        }
        content.append(folders);
        const files = element('div', null, 'naia-hl-files'), boxes = [];
        const listed = (Array.isArray(data.files) ? data.files : []).filter(file => typeof file?.relative === 'string');
        for (const file of listed) {
          const label = element('label', null, 'naia-hl-check'), check = element('input'); check.type = 'checkbox'; check.checked = picked.has(file.relative);
          check.setAttribute('aria-label', `${file.name || '사진'} 불러오기 선택`);
          check.addEventListener('change', () => { check.checked ? picked.add(file.relative) : picked.delete(file.relative); update(); });
          const description = element('span', file.name || '사진');
          if (count(file.size)) description.append(element('small', `${(count(file.size) / (1024 * 1024)).toFixed(1)} MB`));
          label.append(check, description); files.append(label); boxes.push([check, file.relative]);
        }
        if (listed.length) {
          const selectPage = button('이 페이지 사진 선택', () => { for (const [check, path] of boxes) { check.checked = true; picked.add(path); } update(); });
          const clear = button('선택 해제', () => { picked.clear(); for (const [check] of boxes) check.checked = false; update(); });
          const controls = element('div', null, 'naia-hl-import-actions'); controls.append(selectPage, clear); content.append(controls);
        }
        content.append(files);
        if (!folders.childElementCount && !listed.length) content.append(element('p', '이 폴더에서 불러올 사진을 찾지 못했습니다.', 'naia-hl-empty'));
        content.append(navigation(Math.max(1, count(data.page)), Math.max(1, count(data.pages)), next => browse(root, actual, next, picked, subfolders.checked)));
        update();
      } catch (error) {
        if (current(version)) content.replaceChildren(element('p', errorMessage(error, '이 폴더를 읽을 수 없습니다.'), 'naia-hl-inline-error'), button('폴더 목록으로', showRoots), button('다시 불러오기', () => browse(root, relative, wanted, picked, recursive)));
      }
    }
    function showDetail(items) {
      if (!dialog || !items.length || items.length > 2) return;
      mode = 'detail'; const version = clearView();
      dialog.classList.toggle('is-compare', items.length === 2);
      toolbar.append(button('보관함으로', () => loadGallery(page)));
      if (items.length === 1) {
        const insert = button('히스토리에 추가', () => insertHistory(items[0], insert, version), true);
        toolbar.append(insert);
        content.append(element('p', '선택한 사진 한 장을 기존 히스토리에 추가할 수 있습니다.', 'naia-hl-subnote'));
      } else content.append(element('p', '두 사진 비교 · 사진 정보에서 프롬프트와 설정을 확인하세요.', 'naia-hl-path'));
      const grid = element('div', null, 'naia-hl-preview-grid' + (items.length === 2 ? ' is-pair' : '')); content.append(grid);
      for (const item of items) {
        const figure = element('figure', null, 'naia-hl-preview'), media = element('div', null, 'naia-hl-preview-media'), img = element('img');
        img.alt = String(item.name || '사진'); img.decoding = 'async'; media.append(img);
        const size = count(item.width) && count(item.height) ? ` · ${count(item.width)} × ${count(item.height)}` : '';
        const details = element('details'), summary = element('summary', '사진 정보 · 프롬프트 / 설정'), pre = element('pre', '사진 정보를 불러오는 중…');
        details.append(summary, pre); figure.append(media, element('figcaption', `${item.name || '사진'}${size}`), details); grid.append(figure);
        void showAsset(img, item.id, 'preview', version, media);
        void call('metadata', {id: item.id}).then(data => { if (current(version)) pre.textContent = metadataText(data.metadata); }).catch(error => { if (current(version)) pre.textContent = errorMessage(error, '사진 정보를 읽지 못했습니다.'); });
      }
    }
    async function insertHistory(item, target, version) {
      if (target.disabled || !current(version)) return;
      target.disabled = true; target.textContent = '추가하는 중…'; let posted = false, responded = false, timeout = null;
      try {
        const blob = await asset(item.id, 'image');
        if (!current(version)) return;
        if (!blob || typeof blob.arrayBuffer !== 'function') throw new Error('사진 데이터를 읽지 못했습니다.');
        if (blob.size > 64 * 1024 * 1024) throw new Error('기존 히스토리에는 64 MB 이하의 사진을 추가할 수 있습니다.');
        const controller = new global.AbortController();
        timeout = global.setTimeout(() => controller.abort(), 60000);
        posted = true;
        const response = await global.fetch('/api/image/insert-history?label=' + encodeURIComponent(String(item.name || '불러온 사진').slice(0, 120)), {
          method: 'POST', headers: {'Content-Type': blob.type || 'application/octet-stream'}, body: blob, signal: controller.signal,
        });
        const data = await response.json(); responded = true;
        if (!response.ok || data.ok === false || data.error) throw new Error(errorMessage(data.error, '기존 히스토리에 추가하지 못했습니다.'));
        if (!current(version)) return;
        target.textContent = '히스토리에 추가됨'; notify('선택한 사진을 기존 히스토리에 추가했습니다.');
      } catch (error) {
        if (!current(version)) return;
        target.textContent = '히스토리에 추가';
        target.disabled = posted && !responded;
        notify(posted && !responded ? '추가 결과를 확인하지 못했습니다. 기존 히스토리를 확인해 주세요.' : errorMessage(error), true);
      } finally { global.clearTimeout(timeout); }
    }
    function close() {
      session++; global.clearTimeout(pollTimer); pollTimer = null;
      clearView();
      const previous = dialog; dialog = null; content = toolbar = status = progress = progressText = cancelButton = null;
      if (previous) { if (typeof previous.close === 'function' && previous.open) previous.close(); previous.remove(); }
      if (returnFocus?.isConnected) returnFocus.focus(); returnFocus = null;
    }
    async function open() {
      if (destroyed) return;
      if (dialog) { dialog.focus(); return; }
      const sid = ++session; returnFocus = document.activeElement; selected.clear();
      dialog = element('dialog', null, 'naia-hl'); dialog.setAttribute('aria-label', '사진·폴더 보관함');
      const head = element('div', null, 'naia-hl-head'), title = element('h2', '사진·폴더');
      head.append(title, button('닫기', close));
      const note = element('p', '불러온 사진은 앱 보관함에 복사되어 저장됩니다. 원본은 그대로 유지됩니다.', 'naia-hl-note');
      toolbar = element('div', null, 'naia-hl-toolbar'); status = element('p', '', 'naia-hl-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
      progress = element('div', null, 'naia-hl-progress'); progress.hidden = true; progressText = element('span'); progressText.setAttribute('role', 'status');
      cancelButton = button('불러오기 취소', cancelImport); progress.append(progressText, cancelButton);
      content = element('div', null, 'naia-hl-content');
      dialog.append(head, note, toolbar, status, progress, content);
      dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
      document.body.append(dialog);
      if (typeof dialog.showModal === 'function') dialog.showModal(); else { dialog.setAttribute('open', ''); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); }
      notify('보관함을 여는 중…');
      try {
        const data = await call('state');
        if (sid !== session || !dialog) return;
        roots = Array.isArray(data.roots) ? data.roots : []; setJob(data.job); notify(''); await loadGallery(1);
      } catch (error) {
        if (sid === session && dialog) { notify(errorMessage(error), true); toolbar.append(button('다시 연결', refresh)); }
      }
    }
    function patchOpener(candidate) {
      if (!isHistoryOpener(candidate) || changedOpeners.has(candidate)) return;
      changedOpeners.set(candidate, {text: candidate.textContent, onclick: candidate.getAttribute('onclick'), title: candidate.getAttribute('title'), aria: candidate.getAttribute('aria-label')});
      candidate.removeAttribute('onclick'); candidate.onclick = null; candidate.setAttribute('data-naia-history-open', '');
      candidate.textContent = '사진·폴더'; candidate.setAttribute('title', '사진·폴더 불러오기와 비교'); candidate.setAttribute('aria-label', '사진·폴더');
    }
    function scan(node) {
      if (node.nodeType !== 1 && node !== document) return;
      patchOpener(node);
      for (const candidate of node.querySelectorAll?.('[onclick*="openResultFolder"]') || []) patchOpener(candidate);
    }
    const delegated = event => {
      const target = event.target?.closest?.('[data-naia-history-open]');
      if (!target || target.disabled) return;
      event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation(); void open();
    };
    document.addEventListener('click', delegated, true); scan(document);
    const observer = typeof global.MutationObserver === 'function' ? new global.MutationObserver(records => { for (const record of records) for (const added of record.addedNodes) scan(added); }) : null;
    observer?.observe(document.body, {childList: true, subtree: true});
    const controller = {
      open, close,
      destroy() {
        if (destroyed) return;
        destroyed = true; close(); observer?.disconnect(); document.removeEventListener('click', delegated, true); queue.close(); style.remove();
        for (const [target, old] of changedOpeners) {
          if (!target.isConnected || !target.hasAttribute('data-naia-history-open')) continue;
          target.removeAttribute('data-naia-history-open'); target.textContent = old.text;
          for (const [name, value] of [['onclick', old.onclick], ['title', old.title], ['aria-label', old.aria]]) value == null ? target.removeAttribute(name) : target.setAttribute(name, value);
        }
        changedOpeners.clear();
        if (global.__naiaHistoryLibraryController === controller) delete global.__naiaHistoryLibraryController;
      },
    };
    global.__naiaHistoryLibraryController = controller;
    return controller;
  };
})(typeof window === 'undefined' ? globalThis : window);
