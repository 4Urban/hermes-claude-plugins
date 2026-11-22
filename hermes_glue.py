"""The few places the bridge touches Hermes's own configuration. Each falls back to telling the
user the command to run when the Hermes internals it relies on are not importable."""
from . import paths


def register_command_skills_dir():
    """Add the command-skills directory to skills.external_dirs. Returns (changed, message)."""
    target = str(paths.ensure(paths.command_skills_dir()))
    try:
        from hermes_cli.config import load_config_readonly, save_config
    except Exception:
        return False, ("Add %s to skills.external_dirs in config.yaml so translated commands appear as "
                       "slash commands." % target)
    cfg = load_config_readonly() or {}
    skills = cfg.get("skills") or {}
    current = [str(d) for d in (skills.get("external_dirs") or []) if d]
    if target in current:
        return False, "command skills directory already registered"
    save_config({"skills": {"external_dirs": current + [target]}}, merge_existing=True)
    return True, "registered %s in skills.external_dirs" % target


def set_package_enabled(package: str, enable: bool):
    """Enable or disable a generated Agent Plugins package. Returns (ok, message)."""
    try:
        from hermes_cli.plugins_cmd import _set_plugin_enabled
        _set_plugin_enabled(package, enable=enable)
        return True, "%s %s" % ("enabled" if enable else "disabled", package)
    except Exception as exc:
        return False, "run `hermes plugins %s %s` (%s)" % ("enable" if enable else "disable", package, exc)


def config_path():
    return paths.hermes_home() / "config.yaml"


def plugin_selection():
    """(enabled, disabled) package names from Hermes's plugin selection, or None when it cannot be read.

    The hook bridge runs hooks only for packages Hermes would load, so `hermes plugins disable
    claude-<name>` stops a plugin's hooks as well as its skills and MCP servers. Callers treat None
    as "nothing is enabled": an unreadable selection never turns hooks on."""
    home = paths.hermes_home()
    config = None
    try:
        from pm.plugins_state import read_home_selection
    except Exception:
        read_home_selection = None
    try:
        if read_home_selection is not None:
            config = read_home_selection(home)
        elif config_path().exists():
            text = config_path().read_text(encoding="utf-8")
            try:
                import yaml
                config = yaml.safe_load(text)
            except ImportError:  # outside Hermes without PyYAML: JSON is valid YAML, anything else fails closed
                import json
                config = json.loads(text)
    except Exception:
        return None
    plugins = (config or {}).get("plugins") if isinstance(config, dict) else None
    if not isinstance(plugins, dict):
        return set(), set()

    def names(value):
        return {v for v in value if isinstance(v, str)} if isinstance(value, list) else set()

    return names(plugins.get("enabled")), names(plugins.get("disabled"))
