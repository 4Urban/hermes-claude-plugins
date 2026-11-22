"""Claude Plugins: backend routes for the desktop page (desktop/plugin.js).

Mounted by the Hermes web server at ``/api/plugins/claude-plugins/`` while this plugin is in
``plugins.enabled``. The host resolves the profile per request (a secondary profile runs this file
in its own plugin host, bound to its home), so every route works on that profile's state.

Fetching a marketplace or a plugin can take minutes, longer than a request may (a secondary
profile's requests stop at 60 s), so those run as background jobs the page polls. Hook consent is
never part of an install: the page shows the hooks, then sends back the digest the user saw.

Routes:
    GET    /state                          marketplaces, installed plugins, Hermes enable state, jobs
    GET    /jobs/{id}                      one job
    POST   /marketplaces                   {source, name?}            -> job
    POST   /marketplaces/{name}/refresh                               -> job
    DELETE /marketplaces/{name}
    POST   /plugins                        {ref}                      -> job (installed disabled)
    POST   /plugins/{key}/update                                      -> job
    DELETE /plugins/{key}
    POST   /plugins/{key}/consent          {digest}
    DELETE /plugins/{key}/consent
"""
from __future__ import annotations

import contextvars
import importlib.util
import itertools
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()

_PKG_ROOT = Path(__file__).resolve().parent.parent
_PKG_MODULE = "hermes_claude_plugins_dashboard"
_LOAD_LOCK = threading.Lock()


def _package():
    """This plugin's package, loaded by path: the host imports this file with no parent package."""
    mod = sys.modules.get(_PKG_MODULE)
    if mod is not None:
        return mod
    with _LOAD_LOCK:
        mod = sys.modules.get(_PKG_MODULE)
        if mod is None:
            spec = importlib.util.spec_from_file_location(
                _PKG_MODULE, _PKG_ROOT / "__init__.py", submodule_search_locations=[str(_PKG_ROOT)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[_PKG_MODULE] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(_PKG_MODULE, None)
                raise
        return mod


def _mods():
    _package()
    import importlib
    return (importlib.import_module(_PKG_MODULE + ".ops"), importlib.import_module(_PKG_MODULE + ".hermes_glue"),
            importlib.import_module(_PKG_MODULE + ".paths"))


# ---- jobs -------------------------------------------------------------------

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_JOB_IDS = itertools.count(1)
_KEEP_JOBS = 50


def _home_key() -> str:
    return str(_mods()[2].hermes_home())


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _start(kind: str, target: str, fn, *args, **kwargs) -> dict:
    home = _home_key()
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job["_home"] == home and job["status"] == "running" and (job["kind"], job["target"]) == (kind, target):
                return _job_view(job)  # the same operation is already running for this profile
        job = {"id": "%d-%d" % (int(time.time()), next(_JOB_IDS)), "kind": kind, "target": target,
               "status": "running", "lines": [], "error": None, "started_at": time.time(),
               "finished_at": None, "_home": home}
        _JOBS[job["id"]] = job
        for old in sorted(_JOBS.values(), key=lambda j: j["started_at"])[:-_KEEP_JOBS]:
            if old["status"] != "running":
                _JOBS.pop(old["id"], None)

    def run():
        try:
            out = fn(*args, **kwargs)
            job["lines"] = [out] if isinstance(out, str) else list(out or [])
            job["status"] = "done"
        except Exception as exc:
            job["error"] = str(exc) or exc.__class__.__name__
            job["status"] = "error"
        finally:
            job["finished_at"] = time.time()

    # The request's profile scope is a contextvar; a plain thread would run under the launch profile.
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(run,), name="claude-plugins-" + kind, daemon=True).start()
    return _job_view(job)


def _fail(exc: Exception):
    raise HTTPException(status_code=400, detail=str(exc) or exc.__class__.__name__)


def _text(body: dict, key: str, required: bool = True) -> str:
    value = (body or {}).get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise HTTPException(status_code=422, detail="%s must be a non-empty string" % key)
    return value.strip()


# ---- routes -----------------------------------------------------------------

@router.get("/state")
def state():
    ops, glue, paths = _mods()
    try:
        forgotten = ops.reconcile()
        selection = glue.plugin_selection()
        home = str(paths.hermes_home())
        with _JOBS_LOCK:
            jobs = [_job_view(j) for j in _JOBS.values() if j["_home"] == home]
        return {"marketplaces": ops.marketplace_records(), "plugins": ops.plugin_records(selection),
                "selection_known": selection is not None, "forgotten": forgotten,
                "jobs": sorted(jobs, key=lambda j: j["started_at"], reverse=True)[:10]}
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc)


@router.get("/jobs/{job_id}")
def job(job_id: str):
    home = _home_key()
    with _JOBS_LOCK:
        found = _JOBS.get(job_id)
        if found is None or found["_home"] != home:
            raise HTTPException(status_code=404, detail="no such job")
        return _job_view(found)


@router.post("/marketplaces")
def marketplace_add(body: dict = Body(...)):
    ops = _mods()[0]
    source = _text(body, "source")
    name = _text(body, "name", required=False) or None
    return _start("marketplace_add", name or source, ops.marketplace_add, source, name=name)


@router.post("/marketplaces/{name}/refresh")
def marketplace_refresh(name: str):
    ops = _mods()[0]
    return _start("marketplace_update", name, ops.marketplace_update, name)


@router.delete("/marketplaces/{name}")
def marketplace_remove(name: str):
    ops = _mods()[0]
    try:
        return {"lines": [ops.marketplace_remove(name)]}
    except Exception as exc:
        _fail(exc)


@router.post("/plugins")
def plugin_install(body: dict = Body(...)):
    ops = _mods()[0]
    ref = _text(body, "ref")
    # Installed disabled and with hooks off: the page enables the package through plugins.manage
    # (as the desktop Plugins page does) and asks for hook consent separately.
    return _start("install", ref, ops.install, ref, accept_hooks=False, enable=False)


@router.post("/plugins/{key}/update")
def plugin_update(key: str):
    ops = _mods()[0]
    return _start("update", key, ops.update, key)


@router.delete("/plugins/{key}")
def plugin_uninstall(key: str):
    ops = _mods()[0]
    try:
        return {"lines": ops.uninstall(key)}
    except Exception as exc:
        _fail(exc)


@router.post("/plugins/{key}/consent")
def plugin_consent(key: str, body: dict = Body(...)):
    ops = _mods()[0]
    digest = _text(body, "digest")
    try:
        return {"lines": ops.consent(key, expected_digest=digest)}
    except Exception as exc:
        _fail(exc)


@router.delete("/plugins/{key}/consent")
def plugin_revoke(key: str):
    ops = _mods()[0]
    try:
        return {"lines": ops.consent(key, revoke=True)}
    except Exception as exc:
        _fail(exc)
