"""Phase 11: import extraction/resolution, graph API, impact analysis, large-repo limits and grounded LLM summaries."""

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import database, store
from app.main import app
from app.services import dependencies as deps
from app.services import llm
from app.services.dependencies import extract_imports
from tests.helpers import index_files
from tests.test_rag import FakeLLM

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_cache():
    deps._cache.clear()
    yield
    deps._cache.clear()


# ---------------------------------------------------------------- extraction

def specs(content, language):
    return [(line, spec) for line, spec, _ in extract_imports(content, language)]


def test_python_imports():
    code = ("import os, sys as system\nimport a.b.c as abc\nfrom . import views, models\nfrom ..core.db import (\n"
            "    get_db,\n)\n# import commented\nx = 'import not_code'\ndef f():\n    from .lazy import thing\n")
    assert specs(code, "python") == [(2, "."), (3, "..core.db"), (9, ".lazy"), (0, "os"), (0, "sys"), (1, "a.b.c")]
    assert extract_imports("from . import views, models as m\n", "python")[0][2] == ["views", "models"]


def test_javascript_and_typescript_imports():
    code = ("import React from 'react';\nimport {\n  a,\n  b,\n} from \"./utils\";\nexport { x } from '../x.js';\n"
            "import type { T } from '@scope/types/sub';\nconst fs = require('fs');\nconst lazy = () => import('./lazy');\n"
            "import './side-effect.css';\n")
    assert sorted(specs(code, "typescript")) == sorted([(0, "react"), (4, "./utils"), (5, "../x.js"), (6, "@scope/types/sub"),
                                                        (7, "fs"), (8, "./lazy"), (9, "./side-effect.css")])


def test_go_imports():
    code = 'package x\n\nimport "fmt"\nimport (\n\t"net/http"\n\tj "encoding/json"\n\t"github.com/o/app/internal/db"\n)\n'
    assert specs(code, "go") == [(2, "fmt"), (4, "net/http"), (5, "encoding/json"), (6, "github.com/o/app/internal/db")]


# ---------------------------------------------------------------- resolution + graph

FILES = {
    "pkg/__init__.py": "from .core import run\n",
    "pkg/core.py": "import os\nfrom pkg.util import helper\nfrom . import views\n\ndef run():\n    return helper()\n",
    "pkg/util.py": "import requests\n\ndef helper():\n    return 1\n",
    "pkg/views.py": "from .util import helper\n\ndef index():\n    return helper()\n",
    "tests/test_core.py": "from pkg.core import run\nfrom pkg import util\n\ndef test_run():\n    assert run() == 1\n",
    "web/src/app.ts": "import { api } from './api';\nimport React from 'react';\nimport { t } from '@org/i18n';\nimport s from 'shared/strings';\nimport missing from './nope';\n",
    "web/src/api/index.ts": "import axios from 'axios';\nexport const api = axios;\n",
    "packages/shared/strings.js": "module.exports = {};\n",
    "srv/main.go": 'package main\n\nimport (\n\t"fmt"\n\t"github.com/acme/svc/internal/store"\n\t"github.com/stretchr/testify"\n)\n',
    "srv/internal/store/store.go": 'package store\n\nimport "github.com/acme/svc/internal/util"\n\nfunc Get() {}\n',
    "srv/internal/util/util.go": "package util\n\nfunc U() {}\n",
    "native/lib.rs": "use std::io;\nfn main() {}\n",
    "native/other.rs": "fn x() {}\n",
    "README.md": "# Demo\n\nA demo repository.\n",
}


@pytest.fixture
def repo(db):
    return index_files(601, "deps", FILES)


def by_spec(graph, file):
    return {(r.specifier, r.status, r.target) for r in graph.imports if r.file == file}


def test_resolution_classifies_every_import(repo):
    g = deps.get_graph(repo)
    assert by_spec(g, "pkg/core.py") == {("os", "external", "os"), ("pkg.util", "resolved", "pkg/util.py"),
                                         (".", "resolved", "pkg/views.py")}
    assert ("pkg", "resolved", "pkg/util.py") in by_spec(g, "tests/test_core.py")  # from pkg import util -> submodule
    assert by_spec(g, "web/src/app.ts") == {
        ("./api", "resolved", "web/src/api/index.ts"), ("react", "external", "react"),
        ("@org/i18n", "external", "@org/i18n"), ("shared/strings", "inferred", "packages/shared/strings.js"),
        ("./nope", "unresolved", None)}
    assert by_spec(g, "srv/main.go") == {("fmt", "external", "fmt"),
                                         ("github.com/acme/svc/internal/store", "inferred", "srv/internal/store/"),
                                         ("github.com/stretchr/testify", "external", "github.com/stretchr/testify")}
    stats = g.stats()
    assert stats["unsupported_languages"] == {"rust": 2} and stats["unresolved"] == 1
    assert g.reverse["pkg/util.py"].keys() == {"pkg/core.py", "pkg/views.py", "tests/test_core.py"}


def test_graph_api_directory_level(repo):
    body = client.get(f"/api/repos/{repo}/graph", params={"depth": 1}).json()
    edges = {(e["source"], e["target"]): e["weight"] for e in body["edges"]}
    assert edges[("tests/", "pkg/")] == 2 and ("pkg/", "pkg/") not in edges  # aggregated, no self-loops
    assert ("web/", "packages/") in edges and ("srv/", "srv/") not in edges
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["pkg/"]["files"] == 4 and nodes["pkg/"]["in_degree"] == 2 and nodes["pkg/"]["language"] == "python"
    assert body["stats"]["unsupported_languages"] == {"rust": 2} and body["limitations"]


def test_graph_api_file_level_prefix_external_and_limits(repo):
    body = client.get(f"/api/repos/{repo}/graph", params={"level": "file", "path_prefix": "pkg/"}).json()
    assert {n["id"] for n in body["nodes"]} == {"pkg/__init__.py", "pkg/core.py", "pkg/util.py", "pkg/views.py"}
    assert all(e["source"].startswith("pkg/") and e["target"].startswith("pkg/") for e in body["edges"])
    ext = client.get(f"/api/repos/{repo}/graph", params={"level": "file", "include_external": True}).json()
    assert {"pkg:requests", "pkg:react", "pkg:axios"} <= {n["id"] for n in ext["nodes"] if n["type"] == "external"}
    small = client.get(f"/api/repos/{repo}/graph", params={"level": "file", "max_nodes": 3}).json()
    kept = {n["id"] for n in small["nodes"]}
    assert len(kept) == 3 and small["truncated"] and small["total_nodes"] > 3
    assert all(e["source"] in kept and e["target"] in kept for e in small["edges"])
    for params in ({"max_nodes": 0}, {"max_nodes": 301}, {"level": "module"}, {"depth": 9}):
        assert client.get(f"/api/repos/{repo}/graph", params=params).status_code == 422


def test_graph_is_cached_until_reindexed(repo):
    first = deps.get_graph(repo)
    assert deps.get_graph(repo) is first
    with database.session_scope() as s:
        store.get_repository(s, repo).indexed_at = datetime.now(timezone.utc)
    assert deps.get_graph(repo) is not first


# ---------------------------------------------------------------- impact

def test_impact_direct_transitive_and_references(repo):
    r = client.post(f"/api/repos/{repo}/impact", json={"file_path": "pkg/util.py", "symbol": "helper", "depth": 2})
    body = r.json()
    assert [d["file_path"] for d in body["direct_dependents"]] == ["pkg/core.py", "pkg/views.py", "tests/test_core.py"]
    assert body["direct_dependents"][0]["lines"] == [2]
    transitive = {(d["file_path"], d["distance"], d["via"]) for d in body["transitive_dependents"]}
    assert ("pkg/__init__.py", 2, "pkg/core.py") in transitive
    refs = {(x["file_path"], x["line"], x["file_imports_target"]) for x in body["references"]}
    assert ("pkg/core.py", 6, True) in refs and ("pkg/views.py", 4, True) in refs
    assert not any(x["file_path"] == "pkg/util.py" and x["line"] == 3 for x in body["references"])  # the definition
    assert {d["specifier"] for d in body["dependencies"]} == {"requests"}
    assert any("text matches" in l for l in body["limitations"])
    one = client.post(f"/api/repos/{repo}/impact", json={"file_path": "pkg/util.py", "depth": 1}).json()
    assert one["transitive_dependents"] == [] and one["references"] == []


def test_impact_go_package_and_unsupported_language(repo):
    go = client.post(f"/api/repos/{repo}/impact", json={"file_path": "srv/internal/store/store.go"}).json()
    assert [d["file_path"] for d in go["direct_dependents"]] == ["srv/main.go"]
    rust = client.post(f"/api/repos/{repo}/impact", json={"file_path": "native/lib.rs"}).json()
    assert rust["imports_supported"] is False and any("not analyzed" in l for l in rust["limitations"])


@pytest.mark.parametrize("payload, status, code", [
    ({"file_path": "pkg/util.py", "symbol": "nope"}, 404, "symbol_not_found"),
    ({"file_path": "pkg/util.py", "symbol": "a b; drop"}, 400, "invalid_symbol"),
    ({"file_path": "pkg/util.py", "depth": 4}, 400, "invalid_depth"),
    ({"file_path": "pkg/missing.py"}, 404, "file_not_indexed"),
])
def test_impact_errors(repo, payload, status, code):
    r = client.post(f"/api/repos/{repo}/impact", json=payload)
    assert (r.status_code, r.json()["code"]) == (status, code)


# ---------------------------------------------------------------- large repository

def test_large_repository_stays_bounded(db):
    files = {f"mod{i // 100}/file{i}.py": f"from mod{(i // 100 + 1) % 20}.file{(i + 100) % 2000} import x\nimport json\n"
             for i in range(2000)}
    repo_id = index_files(602, "large", files)
    started = time.monotonic()
    body = client.get(f"/api/repos/{repo_id}/graph", params={"level": "file", "max_nodes": 150}).json()
    assert time.monotonic() - started < 10
    assert len(body["nodes"]) == 150 and body["total_nodes"] == 2000 and body["truncated"]
    assert body["stats"]["imports"] == 4000 and body["stats"]["resolved"] == 2000
    kept = {n["id"] for n in body["nodes"]}
    assert all(e["source"] in kept and e["target"] in kept for e in body["edges"])
    directories = client.get(f"/api/repos/{repo_id}/graph").json()
    assert len(directories["nodes"]) == 20 and len(directories["edges"]) == 20  # ring of 20 directories


# ---------------------------------------------------------------- LLM (stub)

@pytest.fixture
def model(monkeypatch):
    stub = FakeLLM("### Overview\nA Python package [S1] whose core imports util [S2]. Bogus [S40].")
    monkeypatch.setattr(llm, "get_llm", lambda: stub)
    return stub


def test_architecture_summary_is_grounded(repo, model):
    body = client.post(f"/api/repos/{repo}/architecture/summary", json={}).json()
    roles = [(s["source_id"], s["role"], s["file_path"]) for s in body["sources"]]
    assert roles[0] == ("S1", "readme", "README.md") and ("hub" in {r[1] for r in roles})
    assert body["citations"]["invalid"] == ["S40"] and "[S40]" not in body["answer"]
    assert body["facts"]["unsupported_languages"] == {"rust": 2} and "pkg/util.py (imported by 3 files)" in body["facts"]["most_imported_files"]
    system, prompt = model.calls[0]
    assert "### Uncertainties" in system and "incomplete" in system
    assert "pkg/util.py (imported by 3 files)" in prompt and "Static import analysis only" in prompt


def test_impact_explanation_is_grounded_and_not_a_guarantee(repo, model):
    model.text = "Changing `helper` affects `run` [S1][S2]."
    body = client.post(f"/api/repos/{repo}/impact/explain", json={"file_path": "pkg/util.py", "symbol": "helper"}).json()
    assert body["sources"][0]["role"] == "target" and body["sources"][0]["symbol_name"] == "helper"
    assert {s["role"] for s in body["sources"][1:]} == {"usage"} and len(body["sources"]) >= 3
    assert body["impact"]["direct_dependents"][0]["file_path"] == "pkg/core.py"
    system, prompt = model.calls[0]
    assert "never claim the list of affected code is complete" in system and "pkg/views.py" in prompt


def test_deterministic_endpoints_work_without_a_key(repo, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    assert client.get(f"/api/repos/{repo}/graph").status_code == 200
    assert client.post(f"/api/repos/{repo}/impact", json={"file_path": "pkg/util.py"}).status_code == 200
    for path, body in (("architecture/summary", {}), ("impact/explain", {"file_path": "pkg/util.py"})):
        r = client.post(f"/api/repos/{repo}/{path}", json=body)
        assert (r.status_code, r.json()["code"]) == (503, "llm_not_configured")


def test_graph_edges_have_a_type_and_file_nodes_a_file(repo):
    body = client.get(f"/api/repos/{repo}/graph", params={"level": "file", "include_external": True}).json()
    assert body["edges"] and all(e["type"] == "imports" for e in body["edges"])
    for n in body["nodes"]:
        assert n["file"] == (n["id"] if n["type"] == "file" else None)
    directory = client.get(f"/api/repos/{repo}/graph").json()
    assert all(n["file"] is None for n in directory["nodes"])
