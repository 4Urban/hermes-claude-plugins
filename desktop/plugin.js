// Claude Plugins: a Desktop page for hermes-claude-plugins.
// Plain disk-plugin ESM (loaded uncompiled): only @hermes/plugin-sdk, react and react/jsx-runtime resolve.
//
// Marketplaces tab: add / refresh / remove Claude Code marketplaces and install their plugins.
// Installed tab: enable or disable each plugin's Hermes package (through plugins.manage, the RPC the
// Capabilities > Plugins page uses), update, remove, and review + accept its hooks.
//
// The backend is this package's dashboard API (/api/plugins/claude-plugins/). Fetches run there as
// jobs this page polls. Hook consent is never part of an install: the page shows every hook command
// and sends back the digest the user saw, so consent cannot cover files that changed meanwhile.
import {
  host, useValue, useQuery, queryClient, usePluginI18n,
  Badge, Button, ConfirmDialog, DisclosureCaret, EmptyState, ErrorState, Input, ListRow, SearchField, Skeleton, Switch,
  Tabs, TabsList, TabsTrigger,
  ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA,
} from '@hermes/plugin-sdk';
import { useEffect, useRef, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';

const ID = 'claude-plugins';
const PATH = '/claude-plugins';
const POLL_MS = 1500;

const locales = {
  en: {
    title: 'Claude Plugins', open: 'Open Claude Plugins',
    description: 'Install Claude Code plugins into this profile. Skills, commands and MCP servers become a Hermes package; hooks run only after you accept them.',
    tabs: { marketplaces: 'Marketplaces', installed: 'Installed' },
    add: { title: 'Add a marketplace', source: 'owner/repo, git URL, URL to marketplace.json, or a local path', name: 'Name (optional)', submit: 'Add' },
    search: 'Search plugins', expandAll: 'Expand all', collapseAll: 'Collapse all',
    pluginCount: (shown, total) => shown === total ? `${total}` : `${shown} / ${total}`, refresh: 'Refresh', remove: 'Remove', install: 'Install', installed: 'Installed', update: 'Update',
    removeMarketplace: name => `Remove the marketplace ${name}?`,
    removeMarketplaceHelp: 'Plugins installed from it must be uninstalled first.',
    uninstall: name => `Uninstall ${name}?`,
    uninstallHelp: 'Removes its Hermes package, its command skills and its hook consent.',
    noMarketplaces: 'No marketplaces yet', noMarketplacesHelp: 'Add one above, for example anthropics/claude-plugins-official.',
    noPlugins: 'No plugins match.', noInstalled: 'No Claude Code plugins installed',
    noInstalledHelp: 'Install one from the Marketplaces tab.',
    unreadable: error => `Unreadable: ${error}`,
    summary: (skills, commands, mcp) => `${skills} skills · ${commands} commands · ${mcp} MCP servers`,
    enabled: 'Enabled in Hermes', enableUnknown: 'Hermes plugin selection is unreadable, so no hooks run.',
    hooks: 'Hooks', hooksOn: 'Accepted', hooksOff: 'Not accepted', review: 'Review and accept', revoke: 'Revoke',
    reviewTitle: name => `Accept the hooks of ${name}?`,
    reviewHelp: 'These shell commands will run on this machine at the listed events, with the files now in the package. Any change to them switches the hooks off again.',
    accept: 'Accept hooks', neverRun: 'no Hermes equivalent, never run',
    notes: 'Not translated', restart: 'Start a new session (or restart the gateway) to load its skills, commands and MCP servers.',
    running: 'Working…', jobDone: what => `${what}: done`, jobFailed: (what, error) => `${what} failed: ${error}`,
    jobKind: { marketplace_add: 'Add marketplace', marketplace_update: 'Refresh', install: 'Install', update: 'Update' },
    toggleFailed: 'Could not change it here; use Capabilities > Plugins.',
    loading: 'Loading Claude Plugins…', unavailable: 'Claude Plugins unavailable', retry: 'Retry',
    missingBackend: 'Install and enable hermes-claude-plugins for this profile, then restart its gateway:',
    forgotten: keys => `Removed elsewhere, forgotten here: ${keys}`,
    keyword: { claude: 'claude', plugins: 'plugins', marketplace: 'marketplace' },
  },
  ko: {
    title: 'Claude 플러그인', open: 'Claude 플러그인 열기',
    description: 'Claude Code 플러그인을 이 프로필에 설치합니다. 스킬·명령·MCP 서버는 Hermes 패키지가 되고, 훅은 승인한 뒤에만 실행됩니다.',
    tabs: { marketplaces: '마켓플레이스', installed: '설치됨' },
    add: { title: '마켓플레이스 추가', source: 'owner/repo, git URL, marketplace.json URL 또는 로컬 경로', name: '이름 (선택)', submit: '추가' },
    search: '플러그인 검색', expandAll: '모두 펼치기', collapseAll: '모두 접기',
    pluginCount: (shown, total) => shown === total ? `${total}` : `${shown} / ${total}`, refresh: '새로고침', remove: '삭제', install: '설치', installed: '설치됨', update: '업데이트',
    removeMarketplace: name => `마켓플레이스 ${name}을(를) 삭제할까요?`,
    removeMarketplaceHelp: '여기서 설치한 플러그인을 먼저 삭제해야 합니다.',
    uninstall: name => `${name}을(를) 삭제할까요?`,
    uninstallHelp: 'Hermes 패키지, 명령 스킬, 훅 승인을 함께 지웁니다.',
    noMarketplaces: '마켓플레이스가 없습니다', noMarketplacesHelp: '위에서 추가하세요. 예: anthropics/claude-plugins-official',
    noPlugins: '일치하는 플러그인이 없습니다.', noInstalled: '설치된 Claude Code 플러그인이 없습니다',
    noInstalledHelp: '마켓플레이스 탭에서 설치하세요.',
    unreadable: error => `읽을 수 없음: ${error}`,
    summary: (skills, commands, mcp) => `스킬 ${skills} · 명령 ${commands} · MCP 서버 ${mcp}`,
    enabled: 'Hermes에서 켜짐', enableUnknown: 'Hermes 플러그인 설정을 읽을 수 없어 훅이 실행되지 않습니다.',
    hooks: '훅', hooksOn: '승인됨', hooksOff: '승인 안 됨', review: '확인 후 승인', revoke: '승인 취소',
    reviewTitle: name => `${name}의 훅을 승인할까요?`,
    reviewHelp: '아래 셸 명령이 표시된 이벤트마다 이 컴퓨터에서 실행됩니다. 승인은 지금 패키지에 있는 파일 기준이며, 바뀌면 훅이 다시 꺼집니다.',
    accept: '훅 승인', neverRun: 'Hermes에 대응 이벤트 없음, 실행 안 됨',
    notes: '변환되지 않은 항목', restart: '스킬·명령·MCP 서버는 새 세션(또는 게이트웨이 재시작)부터 적용됩니다.',
    running: '진행 중…', jobDone: what => `${what}: 완료`, jobFailed: (what, error) => `${what} 실패: ${error}`,
    jobKind: { marketplace_add: '마켓플레이스 추가', marketplace_update: '새로고침', install: '설치', update: '업데이트' },
    toggleFailed: '여기서 바꾸지 못했습니다. Capabilities > Plugins에서 바꾸세요.',
    loading: 'Claude 플러그인 불러오는 중…', unavailable: 'Claude 플러그인을 사용할 수 없습니다', retry: '다시 시도',
    missingBackend: '이 프로필에 hermes-claude-plugins를 설치·활성화하고 게이트웨이를 재시작하세요:',
    forgotten: keys => `다른 곳에서 삭제되어 목록에서 정리됨: ${keys}`,
    keyword: { claude: '클로드', plugins: '플러그인', marketplace: '마켓플레이스' },
  },
};

const errorText = error => String(error?.message || error || 'error');
const enc = encodeURIComponent;

function Failure({ error, retry }) {
  const t = usePluginI18n(ID);
  const missing = /404|403|disabled|not found/i.test(errorText(error));
  return jsx('div', { role: 'alert', children: jsx(ErrorState, {
    title: t('unavailable'),
    description: missing ? t('missingBackend') : errorText(error),
    children: jsxs('div', { className: 'space-y-3', children: [
      missing && jsx('pre', { className: 'whitespace-pre-wrap text-xs', children:
        'hermes plugins install 4Urban/hermes-claude-plugins --enable\nhermes gateway restart' }),
      jsx(Button, { type: 'button', variant: 'outline', onClick: retry, children: t('retry') }),
    ] }),
  }) });
}

function AddMarketplace({ api, busy }) {
  const t = usePluginI18n(ID);
  const [source, setSource] = useState('');
  const [name, setName] = useState('');
  const submit = event => {
    event.preventDefault();
    if (!source.trim()) return;
    api.job('marketplace_add', '/marketplaces', { source: source.trim(), ...(name.trim() ? { name: name.trim() } : {}) },
      () => { setSource(''); setName(''); });
  };
  return jsxs('form', { onSubmit: submit, className: 'space-y-2', children: [
    jsx('h2', { className: 'text-sm font-semibold', children: t('add.title') }),
    jsxs('div', { className: 'flex flex-wrap gap-2', children: [
      jsx(Input, { className: 'min-w-64 flex-1', placeholder: t('add.source'), 'aria-label': t('add.source'),
        value: source, onChange: e => setSource(e.target.value) }),
      jsx(Input, { className: 'w-40', placeholder: t('add.name'), 'aria-label': t('add.name'),
        value: name, onChange: e => setName(e.target.value) }),
      jsx(Button, { type: 'submit', disabled: busy || !source.trim(), children: t('add.submit') }),
    ] }),
  ] });
}

// Open marketplaces are remembered per viewer (ctx.storage); new ones start collapsed, since a
// marketplace can list hundreds of plugins. A search opens every marketplace with a match.
function useOpenMarketplaces(storage) {
  const read = () => { try { return new Set(storage.get('openMarketplaces', [])); } catch { return new Set(); } };
  const [open, setOpen] = useState(read);
  const save = next => { setOpen(next); try { storage.set('openMarketplaces', [...next]); } catch { /* best effort */ } };
  return [open, name => { const next = new Set(open); next.has(name) ? next.delete(name) : next.add(name); save(next); },
    names => save(new Set(names))];
}

function Marketplaces({ data, api, busyKeys, storage }) {
  const t = usePluginI18n(ID);
  const [search, setSearch] = useState('');
  const [removing, setRemoving] = useState(null);
  const [open, toggleOpen, setAll] = useOpenMarketplaces(storage);
  const q = search.trim().toLowerCase();
  const match = p => !q || p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q);
  const names = data.marketplaces.map(m => m.name);
  const allOpen = names.length > 0 && names.every(n => open.has(n));
  return jsxs('div', { className: 'space-y-5', children: [
    jsx(AddMarketplace, { api, busy: busyKeys.has('marketplace_add') }),
    data.marketplaces.length > 0 && jsxs('div', { className: 'flex items-center gap-2', children: [
      jsx(SearchField, { containerClassName: 'flex-1', placeholder: t('search'), value: search, onChange: setSearch }),
      jsx(Button, { type: 'button', size: 'xs', variant: 'ghost', onClick: () => setAll(allOpen ? [] : names),
        children: allOpen ? t('collapseAll') : t('expandAll') }),
    ] }),
    data.marketplaces.length === 0
      ? jsx(EmptyState, { title: t('noMarketplaces'), description: t('noMarketplacesHelp') })
      : data.marketplaces.map(m => {
        const plugins = m.plugins.filter(match);
        if (q && plugins.length === 0 && !m.error) return null;
        const expanded = q ? true : open.has(m.name);
        return jsxs('section', { className: 'space-y-1', children: [
          jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
            jsxs('button', { type: 'button', className: 'flex min-w-0 items-center gap-1.5 text-left',
              'aria-expanded': expanded, disabled: Boolean(q), onClick: () => toggleOpen(m.name), children: [
                jsx(DisclosureCaret, { open: expanded, className: 'text-(--ui-text-quaternary)' }),
                jsx('h2', { className: 'text-sm font-semibold', children: m.name }),
                jsx(Badge, { variant: 'muted', size: 'xs', children: t('pluginCount', plugins.length, m.plugins.length) }),
              ] }),
            jsx('span', { className: 'truncate text-xs text-(--ui-text-tertiary)', children: m.source }),
            jsx('span', { className: 'flex-1' }),
            jsx(Button, { type: 'button', size: 'xs', variant: 'outline', disabled: busyKeys.has('marketplace_update:' + m.name),
              onClick: () => api.job('marketplace_update', `/marketplaces/${enc(m.name)}/refresh`, null), children: t('refresh') }),
            jsx(Button, { type: 'button', size: 'xs', variant: 'ghost', onClick: () => setRemoving(m.name), children: t('remove') }),
          ] }),
          m.error && jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: t('unreadable', m.error) }),
          !expanded ? null : plugins.length === 0 && !m.error
            ? jsx('p', { className: 'py-2 text-xs text-(--ui-text-tertiary)', children: t('noPlugins') })
            : plugins.map(p => {
              const ref = `${p.name}@${m.name}`;
              return jsx(ListRow, {
                title: p.name + (p.version ? ` ${p.version}` : ''), description: p.description,
                action: p.installed
                  ? jsx(Badge, { variant: 'muted', children: t('installed') })
                  : jsx(Button, { type: 'button', size: 'xs', disabled: busyKeys.has('install:' + ref),
                    onClick: () => api.job('install', '/plugins', { ref }, null, ref), children:
                      busyKeys.has('install:' + ref) ? t('running') : t('install') }),
              }, ref);
            }),
        ] }, m.name);
      }),
    jsx(ConfirmDialog, {
      open: removing !== null, onClose: () => setRemoving(null), destructive: true,
      title: removing ? t('removeMarketplace', removing) : '', description: t('removeMarketplaceHelp'),
      confirmLabel: t('remove'),
      onConfirm: () => api.call(`/marketplaces/${enc(removing)}`, { method: 'DELETE' }).finally(() => setRemoving(null)),
    }),
  ] });
}

function HookList({ hooks }) {
  const t = usePluginI18n(ID);
  return jsx('ul', { className: 'space-y-1 text-xs', children: hooks.map((h, i) => jsxs('li', { className: 'break-all', children: [
    jsx('span', { className: 'font-medium', children: h.event + (h.matcher ? ` [${h.matcher}]` : '') + ': ' }),
    jsx('code', { children: h.command }),
    !h.supported && jsx('span', { className: 'text-(--ui-text-tertiary)', children: ` (${t('neverRun')})` }),
  ] }, i)) });
}

function InstalledPlugin({ plugin, api, busyKeys, selectionKnown }) {
  const t = usePluginI18n(ID);
  const [reviewing, setReviewing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const hooks = plugin.hooks || [];
  const below = jsxs('div', { className: 'space-y-2 pt-2', children: [
    hooks.length > 0 && jsxs('div', { className: 'space-y-1', children: [
      jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
        jsx('span', { className: 'text-xs font-semibold', children: t('hooks') }),
        jsx(Badge, { variant: plugin.hooks_consent ? 'success' : 'warn', children: plugin.hooks_consent ? t('hooksOn') : t('hooksOff') }),
        plugin.hooks_consent
          ? jsx(Button, { type: 'button', size: 'xs', variant: 'ghost',
            onClick: () => api.call(`/plugins/${enc(plugin.key)}/consent`, { method: 'DELETE' }), children: t('revoke') })
          : jsx(Button, { type: 'button', size: 'xs', variant: 'outline', onClick: () => setReviewing(true), children: t('review') }),
      ] }),
      jsx(HookList, { hooks }),
    ] }),
    plugin.notes.length > 0 && jsxs('details', { className: 'text-xs text-(--ui-text-tertiary)', children: [
      jsx('summary', { children: `${t('notes')} (${plugin.notes.length})` }),
      jsx('ul', { className: 'list-disc pl-4', children: plugin.notes.map((n, i) => jsx('li', { children: n }, i)) }),
    ] }),
  ] });
  const toggle = on => {
    setToggling(true);
    api.toggle(plugin.package_name, on).finally(() => setToggling(false));
  };
  return jsxs('div', { children: [
    jsx(ListRow, {
      title: `${plugin.key} ${plugin.version || ''}`.trim(),
      description: t('summary', plugin.skills.length, plugin.command_skills.length, plugin.mcp.length),
      hint: selectionKnown ? undefined : t('enableUnknown'),
      action: jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx(Switch, { checked: plugin.enabled === true, disabled: toggling || plugin.enabled === null,
          'aria-label': t('enabled'), onCheckedChange: toggle }),
        jsx(Button, { type: 'button', size: 'xs', variant: 'outline', disabled: busyKeys.has('update:' + plugin.key),
          onClick: () => api.job('update', `/plugins/${enc(plugin.key)}/update`, null), children:
            busyKeys.has('update:' + plugin.key) ? t('running') : t('update') }),
        jsx(Button, { type: 'button', size: 'xs', variant: 'ghost', onClick: () => setRemoving(true), children: t('remove') }),
      ] }),
      below,
    }),
    jsx(ConfirmDialog, {
      open: reviewing, onClose: () => setReviewing(false), title: t('reviewTitle', plugin.key),
      description: t('reviewHelp'), confirmLabel: t('accept'),
      children: jsx('div', { className: 'max-h-80 overflow-auto', children: jsx(HookList, { hooks }) }),
      onConfirm: () => api.call(`/plugins/${enc(plugin.key)}/consent`, { method: 'POST', body: { digest: plugin.hooks_digest } })
        .finally(() => setReviewing(false)),
    }),
    jsx(ConfirmDialog, {
      open: removing, onClose: () => setRemoving(false), destructive: true,
      title: t('uninstall', plugin.key), description: t('uninstallHelp'), confirmLabel: t('remove'),
      onConfirm: () => api.call(`/plugins/${enc(plugin.key)}`, { method: 'DELETE' }).finally(() => setRemoving(false)),
    }),
  ] });
}

function Installed({ data, api, busyKeys }) {
  const t = usePluginI18n(ID);
  if (data.plugins.length === 0) return jsx(EmptyState, { title: t('noInstalled'), description: t('noInstalledHelp') });
  return jsx('div', { className: 'divide-y divide-(--ui-border-subtle)', children: data.plugins.map(p =>
    jsx(InstalledPlugin, { plugin: p, api, busyKeys, selectionKnown: data.selection_known }, p.key)) });
}

function Page({ ctx, scope, current }) {
  const t = usePluginI18n(ID);
  const [tab, setTab] = useState('marketplaces');
  const [jobs, setJobs] = useState([]); // {id, kind, key, onDone}
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const stateKey = [ID, 'state', scope];
  const query = useQuery({
    queryKey: stateKey, retry: false, gcTime: 0, placeholderData: undefined,
    queryFn: async () => {
      if (!current()) throw new Error('Claude Plugins scope changed');
      const result = await ctx.rest('/state');
      if (!current()) throw new Error('Claude Plugins scope changed');
      return result;
    },
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: stateKey });
  const fail = error => { if (current()) host.notify({ kind: 'error', message: errorText(error) }); };
  const profile = () => {
    const p = host.state.profile.get();
    return p && p !== 'default' ? { profile: p } : {};
  };
  const api = {
    call: (path, opts) => ctx.rest(path, opts).then(result => {
      if (current()) {
        for (const line of result?.lines || []) host.notify({ kind: 'success', message: line });
        refresh();
      }
      return result;
    }).catch(fail),
    toggle: (packageName, enable) => host.request('plugins.manage', { action: 'toggle', key: packageName, enable, ...profile() })
      .then(result => {
        if (!result?.ok) throw new Error(t('toggleFailed'));
        if (enable && current()) host.notify({ kind: 'info', message: t('restart') });
      })
      .catch(() => fail(t('toggleFailed')))
      .finally(refresh),
    job: (kind, path, body, onDone, target) => {
      ctx.rest(path, { method: 'POST', ...(body ? { body } : {}) }).then(job => {
        if (!current()) return;
        const key = kind + (target || job.target ? ':' + (target || job.target) : '');
        setJobs(list => list.some(j => j.id === job.id) ? list : [...list, { id: job.id, kind, key, onDone, target: target || job.target }]);
      }).catch(fail);
    },
  };
  // Poll running jobs; each finished one reports, refreshes the lists, and an install enables its package.
  useEffect(() => {
    if (jobs.length === 0) return undefined;
    const timer = setInterval(() => {
      for (const entry of jobsRef.current) {
        ctx.rest(`/jobs/${enc(entry.id)}`).then(job => {
          if (!current() || job.status === 'running') return;
          setJobs(list => list.filter(j => j.id !== entry.id));
          const what = `${t(`jobKind.${entry.kind}`)} ${entry.target || ''}`.trim();
          if (job.status === 'error') {
            host.notify({ kind: 'error', message: t('jobFailed', what, job.error) });
          } else {
            host.notify({ kind: 'success', message: t('jobDone', what) });
            entry.onDone?.();
            // Every plugin installs as the Hermes package claude-<plugin name> (paths.package_name).
            if (entry.kind === 'install' && entry.target) void api.toggle('claude-' + entry.target.split('@')[0], true);
          }
          refresh();
        }).catch(() => setJobs(list => list.filter(j => j.id !== entry.id)));
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [jobs.length]);
  const busyKeys = new Set(jobs.flatMap(j => [j.kind, j.key]));
  let body;
  if (query.isPending) body = jsxs('div', { role: 'status', children: [t('loading'), jsx(Skeleton, { className: 'h-24 w-full' })] });
  else if (query.isError) body = jsx(Failure, { error: query.error, retry: query.refetch });
  else body = tab === 'marketplaces'
    ? jsx(Marketplaces, { data: query.data, api, busyKeys, storage: ctx.storage })
    : jsx(Installed, { data: query.data, api, busyKeys });
  const forgotten = query.data?.forgotten || [];
  return jsxs('main', { className: 'h-full overflow-auto p-5 space-y-5 text-sm text-(--ui-text-primary)', children: [
    jsx('h1', { className: 'text-xl font-semibold', children: t('title') }),
    jsx('p', { className: 'text-(--ui-text-secondary)', children: t('description') }),
    forgotten.length > 0 && jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: t('forgotten', forgotten.join(', ')) }),
    jsx(Tabs, { value: tab, onValueChange: setTab, children: jsxs(TabsList, { children: [
      jsx(TabsTrigger, { value: 'marketplaces', children: t('tabs.marketplaces') }),
      jsx(TabsTrigger, { value: 'installed', children: `${t('tabs.installed')}${query.data ? ` (${query.data.plugins.length})` : ''}` }),
    ] }) }),
    body,
  ] });
}

export default {
  id: ID, name: 'Claude Plugins',
  register(ctx) {
    ctx.i18n.register(locales);
    let disposed = false;
    const scopeNow = () => JSON.stringify([host.state.connectionId.get() || 'local', host.state.profile.get() || 'default']);
    function ScopedPage() {
      const connection = useValue(host.state.connectionId) || 'local';
      const profile = useValue(host.state.profile) || 'default';
      const scope = JSON.stringify([connection, profile]);
      const current = () => !disposed && scopeNow() === scope;
      // Keyed by scope: a profile switch remounts the page, dropping jobs that belong to the old one.
      return jsx(Page, { ctx, scope, current }, scope);
    }
    ctx.onDispose(() => {
      disposed = true;
      void queryClient.cancelQueries({ queryKey: [ID] });
      queryClient.removeQueries({ queryKey: [ID] });
    });
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(ScopedPage, {}) },
    ]);
    let removeLabels;
    const registerLabels = () => {
      removeLabels?.();
      const t = ctx.i18n.t;
      removeLabels = ctx.registerMany([
        { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: t('title'), codicon: 'extensions' } },
        { id: 'open', area: PALETTE_AREA, data: { id: 'claude-plugins.open', label: t('open'),
          keywords: ['claude', 'plugins', 'marketplace', t('keyword.claude'), t('keyword.plugins'), t('keyword.marketplace')],
          run: () => host.navigate(PATH) } },
      ]);
    };
    registerLabels();
    ctx.i18n.onLocaleChange(registerLabels);
  },
};
