"""The operations behind `hermes claude-plugins` and `/claude-plugins`, mirroring `claude plugin`."""
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import fetch, hermes_glue, marketplace as M, paths, store, translate


class OpError(Exception):
    pass


# ---- marketplaces ---------------------------------------------------------

@store.serialised
def marketplace_add(source_text: str, name: str = None) -> str:
    source = fetch.parse_source(source_text)
    state = store.load()
    with tempfile.TemporaryDirectory(prefix="hermes-claude-mkt-") as tmp:
        staged = Path(tmp) / "m"
        rev = fetch.fetch(source, staged)
        data = M.read(staged)
        mkt_name = M.check_name(name or data["name"])
        if mkt_name in state["marketplaces"] and state["marketplaces"][mkt_name].get("source") != source:
            raise OpError("a marketplace named %s already exists from %s; remove it first or pass --name" % (
                mkt_name, fetch.describe(state["marketplaces"][mkt_name]["source"])))
        fetch._replace_dir(staged, M.marketplace_dir(mkt_name))
    state["marketplaces"][mkt_name] = {"source": source, "revision": rev, "updated_at": store.now()}
    store.save(state)
    return "added marketplace %s from %s (%d plugins: %s)" % (
        mkt_name, fetch.describe(source), len(data["plugins"]), ", ".join(p.get("name", "?") for p in data["plugins"]))


@store.serialised
def marketplace_update(name: str = None) -> list:
    state = store.load()
    names = [name] if name else sorted(state["marketplaces"])
    out = []
    for mkt in names:
        rec = state["marketplaces"].get(mkt)
        if not rec:
            raise OpError("no marketplace named %s" % mkt)
        rev = fetch.fetch(rec["source"], M.marketplace_dir(mkt))
        M.read(M.marketplace_dir(mkt))
        rec.update(revision=rev, updated_at=store.now())
        out.append("updated marketplace %s%s" % (mkt, (" to %s" % rev) if rev else ""))
    store.save(state)
    return out


@store.serialised
def marketplace_remove(name: str) -> str:
    state = store.load()
    if name not in state["marketplaces"]:
        raise OpError("no marketplace named %s" % name)
    users = [k for k, v in state["plugins"].items() if v.get("marketplace") == name]
    if users:
        raise OpError("uninstall these first: %s" % ", ".join(users))
    state["marketplaces"].pop(name)
    shutil.rmtree(M.marketplace_dir(name), ignore_errors=True)
    store.save(state)
    return "removed marketplace %s" % name


def marketplace_list() -> list:
    state = store.load()
    out = []
    for name, rec in sorted(state["marketplaces"].items()):
        try:
            plugins = [p.get("name") for p in M.read(M.marketplace_dir(name))["plugins"]]
        except Exception as exc:
            plugins = ["(unreadable: %s)" % exc]
        out.append("%s  %s  updated %s  plugins: %s" % (name, fetch.describe(rec["source"]), rec.get("updated_at", "?"), ", ".join(plugins)))
    return out


# ---- plugins --------------------------------------------------------------

def _split_ref(state: dict, ref: str):
    if "@" in ref:
        plugin, mkt = ref.rsplit("@", 1)
        return plugin, mkt
    hits = []
    for mkt in state["marketplaces"]:
        try:
            if any(p.get("name") == ref for p in M.read(M.marketplace_dir(mkt))["plugins"]):
                hits.append(mkt)
        except Exception:
            continue
    if len(hits) == 1:
        return ref, hits[0]
    if not hits:
        raise OpError("no added marketplace offers %s; add one with `hermes claude-plugins marketplace add <source>`" % ref)
    raise OpError("%s is offered by %s; use %s@<marketplace>" % (ref, ", ".join(hits), ref))


def describe_hooks(hooks: dict) -> list:
    return ["%s%s: %s%s" % (event, (" [%s]" % matcher) if matcher else "", command,
                            "" if event in translate.SUPPORTED_EVENTS else "  (no Hermes equivalent, never run)")
            for event, matcher, command, _t in translate.hook_commands(hooks)]


@store.serialised
def install(ref: str, accept_hooks: bool = False, enable: bool = True) -> list:
    state = store.load()
    plugin, mkt = _split_ref(state, ref)
    if mkt not in state["marketplaces"]:
        raise OpError("marketplace %s has not been added" % mkt)
    mkt_dir = M.marketplace_dir(mkt)
    data = M.read(mkt_dir)
    item = M.entry(data, plugin)
    key = store.plugin_key(plugin, mkt)
    previous = state["plugins"].get(key, {})
    # Every installed plugin owns one package directory (claude-<name>). A plugin of the same name
    # from another marketplace must not replace it: its hooks were accepted for different files.
    pkg_dir = paths.package_dir(plugin)
    for other_key, other in state["plugins"].items():
        if other_key != key and (other.get("package_name") == paths.package_name(plugin)
                                 or other.get("package") == str(pkg_dir)):
            raise OpError("%s is already installed as Hermes package %s; uninstall it before installing %s"
                          % (other_key, paths.package_name(plugin), key))
    adopt = previous.get("package") == str(pkg_dir)
    with tempfile.TemporaryDirectory(prefix="hermes-claude-plugin-") as tmp:
        pdir, rev = M.plugin_dir(mkt_dir, data, item, Path(tmp))
        manifest = translate.load_manifest(pdir, item)
        if manifest["name"] != plugin:
            raise OpError("marketplace entry %s points at a plugin named %s" % (plugin, manifest["name"]))
        version = str(manifest.get("version") or item.get("version") or rev or "0.0.0")
        built = translate.build_package(pdir, manifest, version, owner=key, adopt=adopt)
    package_root = Path(built["package"])
    manifest = translate.load_manifest(package_root, item)
    hooks = translate.hooks_config(package_root, manifest)
    commands = translate.command_files(package_root, manifest)
    notes = list(built["notes"])
    command_skills = translate.build_command_skills(plugin, commands, package_root, notes)
    notes += translate.unsupported_parts(package_root, manifest, hooks)

    digest = translate.hooks_digest(hooks, package_root)
    consented = previous.get("consented_digest") if previous.get("hooks_consent") else None
    if accept_hooks and hooks:
        consented = digest
    rec = {
        "name": plugin, "marketplace": mkt, "version": version, "revision": rev, "package": str(package_root),
        "package_name": paths.package_name(plugin), "skills": built["skills"], "mcp": built["mcp"],
        "commands": [c for c, _p in commands], "command_skills": command_skills,
        "hooks": hooks, "hooks_digest": digest,
        "hooks_consent": bool(hooks) and consented == digest, "consented_digest": consented if consented == digest else None,
        "notes": notes, "installed_at": previous.get("installed_at") or store.now(), "updated_at": store.now(),
    }
    state["plugins"][key] = rec
    store.save(state)

    report = ["installed %s %s as Hermes package %s" % (key, version, rec["package_name"])]
    report.append("  skills: %s" % (", ".join(built["skills"]) or "none"))
    report.append("  MCP servers: %s" % (", ".join(built["mcp"]) or "none"))
    installed_commands = [(translate.command_skill_name(plugin, c), c) for c, _p in commands]
    report.append("  commands: %s" % (", ".join("/%s (was /%s:%s)" % (s, plugin, c) for s, c in installed_commands
                                                if s in command_skills) or "none"))
    if hooks:
        state_txt = "ACCEPTED" if rec["hooks_consent"] else "NOT RUN until you accept them"
        report.append("  hooks (%s):" % state_txt)
        report.extend("    " + line for line in describe_hooks(hooks))
        if not rec["hooks_consent"]:
            report.append("  These run shell commands on this machine. To allow them: hermes claude-plugins consent %s" % key)
    for note in notes:
        report.append("  not translated: %s" % note)
    if command_skills:
        report.append("  " + hermes_glue.register_command_skills_dir()[1])
    if enable:
        report.append("  " + hermes_glue.set_package_enabled(rec["package_name"], True)[1])
    report.append("  Start a new Hermes session (or restart the gateway) to load skills, commands and MCP servers.")
    return report


@store.serialised
def consent(ref: str, revoke: bool = False, expected_digest: str = None) -> list:
    """Accept (or revoke) a plugin's hooks. `expected_digest` is the digest the user was shown: the
    desktop page passes it so consent never covers files that changed after they were reviewed."""
    state = store.load()
    key, rec = store.find_plugin(state, ref)
    hooks = rec.get("hooks") or {}
    if not hooks:
        return ["%s has no hooks" % key]
    if revoke:
        rec.update(hooks_consent=False, consented_digest=None)
        store.save(state)
        return ["hooks of %s will no longer run" % key]
    try:
        on_disk = translate.hooks_digest(hooks, rec["package"])
    except translate.TranslateError as exc:
        raise OpError("%s: %s; reinstall it" % (key, exc))
    if expected_digest is not None and expected_digest != on_disk:
        raise OpError("the hooks of %s changed while you were reviewing them; review them again" % key)
    if on_disk != rec.get("hooks_digest"):
        raise OpError("the files of %s changed since it was installed; run `hermes claude-plugins update %s` "
                      "and review it again before accepting its hooks" % (key, key))
    rec.update(hooks_consent=True, consented_digest=on_disk)
    store.save(state)
    return ["hooks of %s will run in Hermes:" % key] + ["  " + line for line in describe_hooks(hooks)]


@store.serialised
def uninstall(ref: str) -> list:
    state = store.load()
    key, rec = store.find_plugin(state, ref)
    out = [hermes_glue.set_package_enabled(rec["package_name"], False)[1]]
    owner = translate.package_owner(rec["package"])
    if owner in ("", key):
        shutil.rmtree(rec["package"], ignore_errors=True)
    else:
        out.append("left %s in place: it belongs to %s" % (rec["package"], owner))
    translate.remove_command_skills(rec["name"])
    state["plugins"].pop(key)
    store.save(state)
    out.insert(0, "uninstalled %s" % key)
    return out


@store.serialised
def update(ref: str = None) -> list:
    state = store.load()
    keys = [store.find_plugin(state, ref)[0]] if ref else sorted(state["plugins"])
    out = []
    refreshed = set()
    for key in keys:
        mkt = state["plugins"][key]["marketplace"]
        if mkt not in refreshed:
            out.extend(marketplace_update(mkt))
            refreshed.add(mkt)
        out.extend(install(key, accept_hooks=False, enable=False))
    return out


@store.serialised
def sync() -> list:
    """Re-translate every installed plugin from the cached marketplaces, without fetching."""
    state = store.load()
    out = []
    for key in sorted(state["plugins"]):
        out.extend(install(key, accept_hooks=False, enable=False))
    return out or ["nothing installed"]


@store.serialised
def reconcile() -> list:
    """Forget plugins whose package directory is gone (removed through `hermes plugins remove` or
    the desktop Plugins page), along with their command skills. Returns the keys forgotten."""
    state = store.load()
    gone = [k for k, rec in state["plugins"].items() if not Path(rec.get("package") or "").is_dir()]
    for key in gone:
        rec = state["plugins"].pop(key)
        if not any(other.get("name") == rec.get("name") for other in state["plugins"].values()):
            translate.remove_command_skills(rec.get("name", ""))
    if gone:
        store.save(state)
    return gone


# ---- structured views (desktop page) ---------------------------------------

def _hook_rows(hooks: dict) -> list:
    return [{"event": event, "matcher": matcher or "", "command": command,
             "supported": event in translate.SUPPORTED_EVENTS}
            for event, matcher, command, _t in translate.hook_commands(hooks)]


def marketplace_records() -> list:
    state = store.load()
    installed = {(v.get("marketplace"), v.get("name")) for v in state["plugins"].values()}
    out = []
    for name, rec in sorted(state["marketplaces"].items()):
        row = {"name": name, "source": fetch.describe(rec["source"]), "revision": rec.get("revision") or "",
               "updated_at": rec.get("updated_at") or "", "plugins": [], "error": None}
        try:
            for item in M.read(M.marketplace_dir(name))["plugins"]:
                pname = item.get("name") or "?"
                row["plugins"].append({
                    "name": pname, "description": str(item.get("description") or ""),
                    "version": str(item.get("version") or ""), "category": str(item.get("category") or ""),
                    "installed": (name, pname) in installed})
        except Exception as exc:
            row["error"] = str(exc)
        out.append(row)
    return out


def plugin_records(selection=None) -> list:
    """Installed plugins. `selection` is Hermes's (enabled, disabled) package names, or None when
    unknown; `enabled` is then None rather than a guess."""
    state = store.load()
    out = []
    for key, rec in sorted(state["plugins"].items()):
        hooks = rec.get("hooks") or {}
        package = rec.get("package_name") or paths.package_name(rec.get("name", ""))
        enabled = None
        if selection is not None:
            enabled = package in selection[0] and package not in selection[1]
        out.append({
            "key": key, "name": rec.get("name"), "marketplace": rec.get("marketplace"),
            "version": rec.get("version"), "package_name": package, "package": rec.get("package"),
            "enabled": enabled, "skills": rec.get("skills") or [], "mcp": rec.get("mcp") or [],
            "command_skills": rec.get("command_skills") or [], "notes": rec.get("notes") or [],
            "hooks": _hook_rows(hooks), "hooks_digest": rec.get("hooks_digest") if hooks else None,
            "hooks_consent": bool(rec.get("hooks_consent")) if hooks else None,
            "updated_at": rec.get("updated_at") or ""})
    return out


def plugin_list() -> list:
    reconcile()
    state = store.load()
    out = []
    for key, rec in sorted(state["plugins"].items()):
        hooks = rec.get("hooks") or {}
        hook_txt = "no hooks" if not hooks else ("hooks on" if rec.get("hooks_consent") else "hooks off (not accepted)")
        out.append("%s %s  skills %d, commands %d, MCP %d, %s" % (
            key, rec.get("version"), len(rec.get("skills") or []), len(rec.get("command_skills") or []),
            len(rec.get("mcp") or []), hook_txt))
    return out or ["no Claude Code plugins installed"]


def show(ref: str) -> list:
    state = store.load()
    key, rec = store.find_plugin(state, ref)
    out = ["%s %s (Hermes package %s at %s)" % (key, rec.get("version"), rec.get("package_name"), rec.get("package"))]
    out.append("  skills: %s" % (", ".join(rec.get("skills") or []) or "none"))
    out.append("  MCP servers: %s" % (", ".join(rec.get("mcp") or []) or "none"))
    out.append("  commands: %s" % (", ".join("/" + s for s in rec.get("command_skills") or []) or "none"))
    hooks = rec.get("hooks") or {}
    if hooks:
        out.append("  hooks (%s):" % ("accepted" if rec.get("hooks_consent") else "not accepted"))
        out.extend("    " + line for line in describe_hooks(hooks))
    for note in rec.get("notes") or []:
        out.append("  not translated: %s" % note)
    return out


@store.serialised
def import_claude(claude_dir: str = "~/.claude", accept_hooks: bool = False) -> list:
    """Replicate the marketplaces and user-scope plugins of a Claude Code installation."""
    base = Path(os.path.expanduser(claude_dir)) / "plugins"
    try:
        known = json.loads((base / "known_marketplaces.json").read_text(encoding="utf-8"))
        installed = json.loads((base / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpError("cannot read Claude Code plugin records under %s: %s" % (base, exc))
    out = []
    state = store.load()
    for name, rec in sorted(known.items()):
        src = rec.get("source") or {}
        kind = src.get("source")
        if kind == "github":
            text = src["repo"] + (("#" + src["ref"]) if src.get("ref") else "")
        elif kind in ("git", "url"):
            text = src["url"]
        elif kind == "directory":
            text = src["path"]
        else:
            out.append("skipped marketplace %s: source type %r" % (name, kind))
            continue
        if name in state["marketplaces"]:
            out.append("marketplace %s already added" % name)
            continue
        try:
            out.append(marketplace_add(text, name=name))
        except Exception as exc:
            out.append("marketplace %s failed: %s" % (name, exc))
    for key, entries in sorted((installed.get("plugins") or {}).items()):
        scopes = {e.get("scope") for e in entries}
        if "user" not in scopes:
            out.append("skipped %s (installed for a project, not user-wide)" % key)
            continue
        try:
            out.extend(install(key, accept_hooks=accept_hooks))
        except Exception as exc:
            out.append("%s failed: %s" % (key, exc))
    return out
