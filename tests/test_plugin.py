"""Tests for hermes-claude-plugins. No network and no Hermes install needed:
    python3 -m unittest discover -s tests -v
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="hcp-test-"))
os.environ["HERMES_HOME"] = str(TMP / "hermes")


def _load_package():
    spec = importlib.util.spec_from_file_location("hcp", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["hcp"] = module
    spec.loader.exec_module(module)
    return module


hcp = _load_package()
from hcp import cli, fetch, hookbridge, ops, paths, store, translate  # noqa: E402

HOOK_SCRIPT = textwrap.dedent("""\
    import json, os, sys
    data = json.load(sys.stdin)
    event = data["hook_event_name"]
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if event == "SessionStart":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
              "additionalContext": "START from %s source=%s" % (os.path.basename(root), data.get("source"))}}))
    elif event == "UserPromptSubmit":
        print("PROMPT saw: " + data["prompt"])
    elif event == "Stop":
        if not data.get("stop_hook_active"):
            print(json.dumps({"decision": "block", "reason": "save your work first"}))
    elif event == "PreToolUse":
        if "rm -rf" in json.dumps(data.get("tool_input")):
            print("no recursive deletes", file=sys.stderr)
            sys.exit(2)
    elif event == "SessionEnd":
        open(os.path.join(os.environ["MARKER_DIR"], "ended"), "w").write(data["session_id"])
    """)


def make_marketplace(base: Path, hooks_extra=None) -> Path:
    mkt = base / "mkt"
    plug = mkt / "plugins" / "demo"
    (mkt / ".claude-plugin").mkdir(parents=True)
    (mkt / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "testmkt", "owner": {"name": "t"},
        "plugins": [{"name": "demo", "source": "./plugins/demo", "description": "demo plugin"}]}))
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "demo", "version": "1.2.3", "description": "A demo", "author": {"name": "Tester"}, "license": "MIT"}))
    (plug / "skills" / "greet").mkdir(parents=True)
    (plug / "skills" / "greet" / "SKILL.md").write_text(
        "---\nname: greet\ndescription: Say hello\n---\nRun ${CLAUDE_PLUGIN_ROOT}/bin/greet\n")
    (plug / "commands" / "sub").mkdir(parents=True)
    (plug / "commands" / "hello.md").write_text(
        "---\ndescription: Greet someone\nargument-hint: \"<name>\"\n---\n\nSay hello to $ARGUMENTS using ${CLAUDE_PLUGIN_ROOT}/bin/greet.\n")
    (plug / "commands" / "sub" / "deep.md").write_text("Deep command body.\n")
    (plug / "agents").mkdir()
    (plug / "agents" / "helper.md").write_text("---\nname: helper\n---\nA subagent.\n")
    (plug / "hooks").mkdir()
    (plug / "hooks" / "hook.py").write_text(HOOK_SCRIPT)
    cmd = "%s \"${CLAUDE_PLUGIN_ROOT}/hooks/hook.py\"" % sys.executable
    hooks = {
        "SessionStart": [{"matcher": "startup|resume", "hooks": [{"type": "command", "command": cmd, "timeout": 10}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": cmd}]}],
        "Stop": [{"hooks": [{"type": "command", "command": cmd}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": cmd}]}],
        "PreCompact": [{"hooks": [{"type": "command", "command": cmd}]}],
    }
    hooks.update(hooks_extra or {})
    (plug / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}))
    (plug / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "local": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/server", "args": ["--root", "${CLAUDE_PLUGIN_ROOT}"]},
        "remote": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"X-Test": "1"}},
        "legacy": {"type": "sse", "url": "https://mcp.example.com/sse"},
        "absolute": {"command": "/usr/local/bin/thing"},
        "envy": {"command": "npx", "args": ["${API_TOKEN}"]},
    }}))
    return mkt


def select_plugins(enabled=(), disabled=()):
    """Write Hermes's plugin selection (config.yaml; JSON is valid YAML)."""
    paths.ensure(paths.hermes_home())
    (paths.hermes_home() / "config.yaml").write_text(json.dumps(
        {"plugins": {"enabled": list(enabled), "disabled": list(disabled)}}))


def make_plugin(mkt: Path, name: str, hook_cmd: str = None):
    """Add another plugin (optionally with one SessionStart hook) to a marketplace tree."""
    plug = mkt / "plugins" / name
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
    if hook_cmd:
        (plug / "hooks").mkdir()
        (plug / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": hook_cmd}]}]}}))
    data = json.loads((mkt / ".claude-plugin" / "marketplace.json").read_text())
    data["plugins"].append({"name": name, "source": "./plugins/" + name})
    (mkt / ".claude-plugin" / "marketplace.json").write_text(json.dumps(data))
    return plug


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(paths.hermes_home(), ignore_errors=True)
        self.work = Path(tempfile.mkdtemp(dir=TMP))
        self.mkt = make_marketplace(self.work)
        os.environ["MARKER_DIR"] = str(self.work)
        ops.marketplace_add(str(self.mkt))
        select_plugins(enabled=["claude-demo"])


class TestSources(unittest.TestCase):
    def test_parse_source(self):
        self.assertEqual(fetch.parse_source("owner/repo")["source"], "github")
        self.assertEqual(fetch.parse_source("owner/repo#v2")["ref"], "v2")
        self.assertEqual(fetch.parse_source("https://github.com/o/r.git")["repo"], "o/r")
        self.assertEqual(fetch.parse_source("https://gitlab.com/o/r.git")["source"], "git")
        self.assertEqual(fetch.parse_source("https://x.example/marketplace.json")["source"], "json-url")
        self.assertEqual(fetch.parse_source(str(TMP))["source"], "local")
        with self.assertRaises(fetch.FetchError):
            fetch.parse_source("not a source")

    def test_tarball_escape_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("../evil.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(fetch.FetchError):
            fetch._safe_extract(buf.getvalue(), TMP / "extract")


class TestInstall(Base):
    def test_marketplace_list(self):
        self.assertIn("testmkt", "\n".join(ops.marketplace_list()))

    def test_install_translates(self):
        report = "\n".join(ops.install("demo"))
        pkg = paths.package_dir("demo")
        manifest = json.loads((pkg / "plugin.json").read_text())
        self.assertEqual(manifest["$schema"], translate.AGENT_PLUGIN_SCHEMA)
        self.assertEqual(manifest["name"], "claude-demo")
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertIn(str(pkg), (pkg / "skills" / "greet" / "SKILL.md").read_text())
        mcp = json.loads((pkg / "mcp.json").read_text())
        self.assertEqual(mcp["$schema"], translate.AGENT_MCP_SCHEMA)
        self.assertEqual(mcp["mcpServers"]["local"], {"type": "stdio", "command": "./bin/server", "args": ["--root", "${PLUGIN_ROOT}"]})
        self.assertEqual(mcp["mcpServers"]["remote"]["type"], "streamable-http")
        self.assertNotIn("legacy", mcp["mcpServers"])
        self.assertNotIn("absolute", mcp["mcpServers"])
        self.assertNotIn("envy", mcp["mcpServers"])
        for text in ("SSE transport", "bare executable", "environment placeholders", "agents/", "PreCompact hooks"):
            self.assertIn(text, report)
        self.assertIn("NOT RUN until you accept them", report)
        hello = (paths.command_skills_dir() / "demo-hello" / "SKILL.md").read_text()
        self.assertIn("name: demo-hello", hello)
        self.assertIn('description: "Greet someone"', hello)
        self.assertIn("$ARGUMENTS", hello)
        self.assertIn("(expected: <name>)", hello)
        self.assertIn(str(pkg) + "/bin/greet", hello)
        self.assertTrue((paths.command_skills_dir() / "demo-sub-deep" / "SKILL.md").is_file())

    def test_hooks_need_consent(self):
        ops.install("demo")
        bridge = hookbridge.Bridge()
        self.assertEqual(bridge.entries("SessionStart"), [])
        ops.consent("demo@testmkt")
        self.assertEqual(len(bridge.entries("SessionStart")), 1)
        ops.consent("demo", revoke=True)
        self.assertEqual(bridge.entries("SessionStart"), [])

    def test_changed_hooks_withdraw_consent(self):
        ops.install("demo", accept_hooks=True)
        self.assertTrue(store.load()["plugins"]["demo@testmkt"]["hooks_consent"])
        ops.sync()
        self.assertTrue(store.load()["plugins"]["demo@testmkt"]["hooks_consent"], "unchanged hooks keep consent")
        shutil.rmtree(self.mkt)
        make_marketplace(self.work, {"PostToolUse": [{"hooks": [{"type": "command", "command": "true"}]}]})
        ops.update("demo")
        self.assertFalse(store.load()["plugins"]["demo@testmkt"]["hooks_consent"])

    def test_uninstall(self):
        ops.install("demo")
        ops.uninstall("demo")
        self.assertFalse(paths.package_dir("demo").exists())
        self.assertFalse((paths.command_skills_dir() / "demo-hello").exists())
        self.assertEqual(store.load()["plugins"], {})
        self.assertIn("removed", ops.marketplace_remove("testmkt"))

    def test_remove_marketplace_in_use(self):
        ops.install("demo")
        with self.assertRaises(ops.OpError):
            ops.marketplace_remove("testmkt")

    def test_slash_command(self):
        ops.install("demo")
        self.assertIn("demo@testmkt 1.2.3", cli.handle_slash("list"))
        self.assertIn("hooks (not accepted)", cli.handle_slash("show demo"))
        self.assertIn("hermes claude-plugins install", cli.handle_slash("bogus"))
        self.assertIn("is not installed", cli.handle_slash("show nope"))


class TestHookBridge(Base):
    def setUp(self):
        super().setUp()
        ops.install("demo", accept_hooks=True)
        self.bridge = hookbridge.Bridge()

    def test_session_start_and_prompt(self):
        first = self.bridge.pre_llm_call(session_id="s1", user_message="hi there", conversation_history=[], is_first_turn=True)
        self.assertIn("START from claude-demo source=startup", first["context"])
        self.assertIn("PROMPT saw: hi there", first["context"])
        second = self.bridge.pre_llm_call(session_id="s1", user_message="again", conversation_history=[{"role": "user", "content": "hi there"}])
        self.assertNotIn("START", second["context"])
        lines = Path(self.bridge._transcript_path("s1")).read_text().splitlines()
        self.assertEqual([json.loads(l)["type"] for l in lines], ["user", "user"])

    def test_stop_reason_delivered_next_turn(self):
        self.bridge.on_session_end(session_id="s2", completed=True)
        nxt = self.bridge.pre_llm_call(session_id="s2", user_message="ok", conversation_history=[])
        self.assertIn("save your work first", nxt["context"])
        self.bridge.on_session_end(session_id="s2", completed=True)  # stop_hook_active now true
        again = self.bridge.pre_llm_call(session_id="s2", user_message="ok", conversation_history=[])
        self.assertNotIn("save your work first", again["context"])

    def test_pre_tool_call(self):
        blocked = self.bridge.pre_tool_call(tool_name="terminal", args={"command": "rm -rf /tmp/x"}, session_id="s3")
        self.assertEqual(blocked["action"], "block")
        self.assertIn("no recursive deletes", blocked["message"])
        self.assertIsNone(self.bridge.pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="s3"))
        self.assertIsNone(self.bridge.pre_tool_call(tool_name="read_file", args={"path": "rm -rf"}, session_id="s3"))

    def test_session_end(self):
        self.bridge.pre_llm_call(session_id="s4", user_message="x", conversation_history=[], is_first_turn=True)
        self.bridge.on_session_finalize(session_id="s4")
        self.assertEqual((self.work / "ended").read_text(), "s4")
        self.assertFalse(Path(self.bridge._transcript_path("s4")).exists())


class TestSecurity(Base):
    """Regression tests for the four hardening fixes."""

    def _marketplace_named(self, name):
        bad = self.work / ("bad-%d" % abs(hash(name)))
        (bad / ".claude-plugin").mkdir(parents=True)
        (bad / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": name, "plugins": []}))
        return bad

    # 1. a marketplace chooses its own name; it must not pick the directory that gets replaced
    def test_marketplace_name_cannot_escape(self):
        victim = paths.hermes_home() / "plugins" / "victim"
        victim.mkdir(parents=True)
        (victim / "keep").write_text("x")
        for name in ("../../plugins/victim", "../victim", str(victim), "a/b", "..", ".hidden", ""):
            with self.assertRaises(Exception, msg=name):
                ops.marketplace_add(str(self._marketplace_named(name)))
        with self.assertRaises(Exception):
            ops.marketplace_add(str(self.mkt), name="../../plugins/victim")
        self.assertEqual((victim / "keep").read_text(), "x")
        self.assertEqual(sorted(store.load()["marketplaces"]), ["testmkt"])
        with self.assertRaises(Exception):
            ops.marketplace_remove("../../plugins/victim")
        self.assertTrue(victim.is_dir())

    # 2. consent covers the scripts a hook runs, not only the hook's command line
    def test_changed_script_withdraws_consent(self):
        ops.install("demo", accept_hooks=True)
        bridge = hookbridge.Bridge()
        self.assertEqual(len(bridge.entries("SessionStart")), 1)
        (self.mkt / "plugins" / "demo" / "hooks" / "hook.py").write_text("print('changed')\n")
        ops.update("demo")
        rec = store.load()["plugins"]["demo@testmkt"]
        self.assertFalse(rec["hooks_consent"])
        self.assertEqual(bridge.entries("SessionStart"), [])

    def test_tampered_package_stops_hooks(self):
        ops.install("demo", accept_hooks=True)
        bridge = hookbridge.Bridge()
        self.assertEqual(len(bridge.entries("PreToolUse")), 1)
        (paths.package_dir("demo") / "hooks" / "hook.py").write_text("print('tampered')\n")
        self.assertEqual(bridge.entries("PreToolUse"), [], "edited files on disk are not run")
        with self.assertRaises(ops.OpError):
            ops.consent("demo")
        ops.sync()  # reinstall from the marketplace restores the accepted files
        self.assertTrue(store.load()["plugins"]["demo@testmkt"]["hooks_consent"])
        self.assertEqual(len(bridge.entries("PreToolUse")), 1)

    # 3. a same-named plugin from another marketplace must not take over the package
    def test_same_name_other_marketplace_refused(self):
        ops.install("demo", accept_hooks=True)
        before = translate.tree_digest(paths.package_dir("demo"))
        other = self.work / "other"
        shutil.copytree(self.mkt, other)
        data = json.loads((other / ".claude-plugin" / "marketplace.json").read_text())
        data["name"] = "othermkt"
        (other / ".claude-plugin" / "marketplace.json").write_text(json.dumps(data))
        (other / "plugins" / "demo" / "hooks" / "hook.py").write_text("print('evil')\n")
        ops.marketplace_add(str(other))
        with self.assertRaises(ops.OpError):
            ops.install("demo@othermkt")
        self.assertEqual(translate.tree_digest(paths.package_dir("demo")), before)
        self.assertEqual(sorted(store.load()["plugins"]), ["demo@testmkt"])
        self.assertEqual(len(hookbridge.Bridge().entries("SessionStart")), 1)

    def test_foreign_package_dir_left_alone(self):
        foreign = paths.packages_dir() / "claude-mine"
        foreign.mkdir(parents=True)
        (foreign / "plugin.yaml").write_text("name: claude-mine\n")
        make_plugin(self.mkt, "mine")
        ops.marketplace_update("testmkt")
        with self.assertRaises(Exception):
            ops.install("mine")
        self.assertEqual((foreign / "plugin.yaml").read_text(), "name: claude-mine\n")

    def test_command_skill_name_clash_not_overwritten(self):
        ops.install("demo")
        hello = paths.command_skills_dir() / "demo-hello" / "SKILL.md"
        before = hello.read_text()
        plug = make_plugin(self.mkt, "demo-x")
        (plug / "commands").mkdir()
        (plug / "commands" / "y.md").write_text("fine\n")
        clash = paths.command_skills_dir() / "demo-x-y"
        clash.mkdir(parents=True)
        (clash / "SKILL.md").write_text("someone else's")
        (clash / ".claude-plugin-owner").write_text("demo\n")
        ops.marketplace_update("testmkt")
        report = "\n".join(ops.install("demo-x"))
        self.assertIn("belongs to another plugin", report)
        self.assertEqual((clash / "SKILL.md").read_text(), "someone else's")
        self.assertEqual(hello.read_text(), before)

    # 4. hooks follow Hermes's enable/disable of the package
    def test_disabled_package_runs_no_hooks(self):
        ops.install("demo", accept_hooks=True)
        bridge = hookbridge.Bridge()
        self.assertEqual(len(bridge.entries("SessionStart")), 1)
        select_plugins(enabled=["claude-demo"], disabled=["claude-demo"])
        os.utime(paths.hermes_home() / "config.yaml", (1, 1))  # force a visible mtime change
        self.assertEqual(bridge.entries("SessionStart"), [])
        select_plugins(enabled=[])
        self.assertEqual(hookbridge.Bridge().entries("SessionStart"), [])
        (paths.hermes_home() / "config.yaml").write_text("{not json or yaml: [")
        self.assertEqual(hookbridge.Bridge().entries("SessionStart"), [], "unreadable selection fails closed")
        select_plugins(enabled=["claude-demo"])
        self.assertEqual(len(hookbridge.Bridge().entries("SessionStart")), 1)
        self.assertIsNone(hookbridge.Bridge().pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="d"))
        os.environ["HERMES_SAFE_MODE"] = "1"
        try:
            self.assertEqual(hookbridge.Bridge().entries("SessionStart"), [])
        finally:
            os.environ.pop("HERMES_SAFE_MODE")


class TestHermesLoadedFiles(Base):
    """A Claude plugin must not smuggle in files Hermes loads as its own plugin code."""

    def test_stripped(self):
        plug = self.mkt / "plugins" / "demo"
        (plug / "plugin.yaml").write_text("name: claude-demo\n")
        (plug / "__init__.py").write_text("def register(ctx):\n    raise SystemExit('ran')\n")
        (plug / "plugin.js").write_text("export default {}\n")
        (plug / "desktop").mkdir()
        (plug / "desktop" / "plugin.js").write_text("export default {}\n")
        (plug / "dashboard").mkdir()
        (plug / "dashboard" / "manifest.json").write_text('{"api": "plugin_api.py"}')
        (plug / "dashboard" / "plugin_api.py").write_text("raise SystemExit('ran')\n")
        ops.marketplace_update("testmkt")
        report = "\n".join(ops.install("demo"))
        pkg = paths.package_dir("demo")
        for rel in translate.HERMES_LOADED_FILES:
            self.assertFalse((pkg / rel).exists(), rel)
        for rel in ("plugin.yaml", "plugin.js", "desktop/plugin.js", "dashboard/manifest.json"):
            self.assertIn("%s removed" % rel, report)
        self.assertTrue((pkg / "plugin.json").is_file())


class TestReconcileAndRecords(Base):
    def test_package_removed_elsewhere(self):
        ops.install("demo")
        shutil.rmtree(paths.package_dir("demo"))  # as `hermes plugins remove claude-demo` would
        self.assertEqual(ops.reconcile(), ["demo@testmkt"])
        self.assertEqual(store.load()["plugins"], {})
        self.assertFalse((paths.command_skills_dir() / "demo-hello").exists())

    def test_records(self):
        ops.install("demo")
        mkts = ops.marketplace_records()
        self.assertEqual(mkts[0]["name"], "testmkt")
        self.assertTrue(mkts[0]["plugins"][0]["installed"])
        rows = ops.plugin_records((({"claude-demo"}), set()))
        self.assertTrue(rows[0]["enabled"])
        self.assertFalse(rows[0]["hooks_consent"])
        self.assertIn("SessionStart", {h["event"] for h in rows[0]["hooks"]})
        self.assertIsNone(ops.plugin_records(None)[0]["enabled"])

    def test_consent_needs_the_digest_shown(self):
        ops.install("demo")
        with self.assertRaises(ops.OpError):
            ops.consent("demo", expected_digest="0" * 64)
        ops.consent("demo", expected_digest=store.load()["plugins"]["demo@testmkt"]["hooks_digest"])
        self.assertTrue(store.load()["plugins"]["demo@testmkt"]["hooks_consent"])

    def test_lock_is_reentrant(self):
        with store.locked():
            with store.locked():
                ops.install("demo")
        self.assertIn("demo@testmkt", store.load()["plugins"])


try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi not installed (run with Hermes's Python)")
class TestDashboardApi(Base):
    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("hcp_plugin_api", ROOT / "dashboard" / "plugin_api.py")
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        app = fastapi.FastAPI()
        app.include_router(api.router, prefix="/api/plugins/claude-plugins")
        self.client = TestClient(app)

    def call(self, method, path, **kw):
        return self.client.request(method, "/api/plugins/claude-plugins" + path, **kw)

    def wait(self, job):
        import time
        for _ in range(200):
            job = self.call("GET", "/jobs/" + job["id"]).json()
            if job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_flow(self):
        st = self.call("GET", "/state").json()
        self.assertEqual([m["name"] for m in st["marketplaces"]], ["testmkt"])
        job = self.wait(self.call("POST", "/plugins", json={"ref": "demo@testmkt"}).json())
        self.assertEqual(job["status"], "done", job)
        plugin = self.call("GET", "/state").json()["plugins"][0]
        self.assertEqual(plugin["key"], "demo@testmkt")
        self.assertFalse(plugin["hooks_consent"], "install never accepts hooks")
        bad = self.call("POST", "/plugins/demo@testmkt/consent", json={"digest": "nope"})
        self.assertEqual(bad.status_code, 400)
        ok = self.call("POST", "/plugins/demo@testmkt/consent", json={"digest": plugin["hooks_digest"]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(self.call("GET", "/state").json()["plugins"][0]["hooks_consent"])
        self.assertEqual(self.call("DELETE", "/plugins/demo@testmkt/consent").status_code, 200)
        self.assertEqual(self.call("DELETE", "/plugins/demo@testmkt").status_code, 200)
        self.assertEqual(self.call("GET", "/state").json()["plugins"], [])
        self.assertEqual(self.call("DELETE", "/marketplaces/testmkt").status_code, 200)

    def test_errors(self):
        job = self.wait(self.call("POST", "/marketplaces", json={"source": str(self.mkt), "name": "../x"}).json())
        self.assertEqual(job["status"], "error")
        self.assertEqual(self.call("POST", "/plugins", json={}).status_code, 422)
        self.assertEqual(self.call("GET", "/jobs/nope").status_code, 404)
        self.assertEqual(self.call("DELETE", "/plugins/nope").status_code, 400)


class TestRegister(unittest.TestCase):
    def test_register(self):
        seen = {"cli": [], "cmd": [], "hooks": []}

        class Ctx:
            def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
                seen["cli"].append(name)

            def register_command(self, name, handler, description="", args_hint=""):
                seen["cmd"].append(name)

            def register_hook(self, name, cb):
                seen["hooks"].append(name)

        hcp.register(Ctx())
        self.assertEqual(seen["cli"], ["claude-plugins"])
        self.assertEqual(seen["cmd"], ["claude-plugins"])
        self.assertEqual(sorted(seen["hooks"]), sorted(["pre_llm_call", "on_session_end", "pre_tool_call",
                                                        "post_tool_call", "on_session_finalize"]))


SHAREDBRAIN = Path(os.path.expanduser("~/projects/claude-mempalace-sharedbrain"))


@unittest.skipUnless((SHAREDBRAIN / ".claude-plugin" / "marketplace.json").is_file(), "sharedbrain checkout not present")
class TestRealPlugin(unittest.TestCase):
    """The mempalace-sharedbrain plugin through the bridge: commands, hooks, and its session-start output."""

    def test_sharedbrain(self):
        shutil.rmtree(paths.hermes_home(), ignore_errors=True)
        ops.marketplace_add(str(SHAREDBRAIN))
        select_plugins(enabled=["claude-mempalace-sharedbrain"])
        report = "\n".join(ops.install("mempalace-sharedbrain", accept_hooks=True))
        self.assertIn("/mempalace-sharedbrain-inbox", report)
        if "modules" in json.loads((SHAREDBRAIN / "hooks" / "hooks.json").read_text()):
            self.assertIn("a Claude Code mod", report)
        self.assertIn("PreCompact hooks", report)
        cfg = TMP / "sb-config.json"
        cfg.write_text(json.dumps({"identity": {"host": "testhost", "harness": "hermes"}, "hub": {"transport": "none"}}))
        os.environ["MEMPALACE_SHAREDBRAIN_CONFIG"] = str(cfg)
        os.environ["MEMPALACE_SHAREDBRAIN_STATE"] = str(TMP / "sb-state")
        try:
            out = hookbridge.Bridge().pre_llm_call(session_id="sb1", user_message="hello", conversation_history=[], is_first_turn=True)
        finally:
            os.environ.pop("MEMPALACE_SHAREDBRAIN_CONFIG")
            os.environ.pop("MEMPALACE_SHAREDBRAIN_STATE")
        self.assertIn("MEMPALACE SHARED BRAIN", out["context"])
        self.assertIn("testhost:hermes:", out["context"])


if __name__ == "__main__":
    unittest.main()
