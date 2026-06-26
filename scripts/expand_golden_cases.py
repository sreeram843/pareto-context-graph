#!/usr/bin/env python3
"""Expand golden eval cases from co-change graph neighbours."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pareto_context_graph.store import Store


def gen_cases(
    repo: Path,
    repo_key: str,
    existing_ids: set[str],
    target: int,
    prefix: str,
) -> list[dict]:
    store = Store(repo)
    try:
        all_files = [f for f in store.all_files() if "/" in f]
        if repo_key == "fastapi":
            candidates = [
                f
                for f in all_files
                if f.endswith(".py")
                and f.startswith("fastapi/")
                and "/test" not in f
            ]
        elif repo_key == "httpx":
            candidates = [
                f
                for f in all_files
                if f.endswith(".py")
                and f.startswith("httpx/")
            ]
        else:
            candidates = [
                f
                for f in all_files
                if f.endswith(".go")
                and not f.endswith("_test.go")
                and "/vendor/" not in f
            ]
        cases: list[dict] = []
        for seed in sorted(candidates):
            if len(cases) + len(existing_ids) >= target:
                break
            case_id = f"{prefix}_{seed.replace('/', '_').replace('.', '_')}"
            if case_id in existing_ids:
                continue
            neigh = store.top_neighbours(seed, limit=12)
            expected = [p for p, w in neigh if p != seed and w >= 1][:3]
            if len(expected) < 2:
                neighbours = store.neighbours(seed, min_weight=1)
                expected = [p for p, w in neighbours if p != seed][:3]
            if len(expected) < 2:
                continue
            cases.append(
                {
                    "case_id": case_id,
                    "repo_key": repo_key,
                    "seed_files": [seed],
                    "query": "",
                    "expected_top_files": expected,
                    "tier": 1,
                    "token_budget": 6000,
                    "max_depth": 1,
                    "category": "co_change",
                    "notes": f"Auto-generated from graph neighbours of {seed}.",
                }
            )
            existing_ids.add(case_id)
        return cases
    finally:
        store.close()


CONCEPT_QUERIES: dict[str, list[tuple[str, list[str]]]] = {
    "fastapi": [
        ("WebSocket connection handling", ["fastapi/websockets.py", "fastapi/routing.py", "fastapi/applications.py"]),
        ("background tasks run after response", ["fastapi/background.py", "fastapi/routing.py", "fastapi/dependencies/utils.py"]),
        ("JSON response serialization encoders", ["fastapi/encoders.py", "fastapi/responses.py", "fastapi/routing.py"]),
        ("static files mount StaticFiles", ["fastapi/staticfiles.py", "fastapi/applications.py", "fastapi/routing.py"]),
        ("Jinja2 templates TemplateResponse", ["fastapi/templating.py", "fastapi/applications.py", "fastapi/routing.py"]),
        ("Request and Response starlette wrappers", ["fastapi/requests.py", "fastapi/responses.py", "fastapi/routing.py"]),
        ("APIRouter include_router mounting", ["fastapi/routing.py", "fastapi/applications.py", "fastapi/params.py"]),
        ("HTTPException validation error handlers", ["fastapi/exceptions.py", "fastapi/exception_handlers.py", "fastapi/routing.py"]),
        ("param_functions Path Query Body Header", ["fastapi/param_functions.py", "fastapi/params.py", "fastapi/dependencies/utils.py"]),
        ("trusted host middleware security", ["fastapi/middleware/trustedhost.py", "fastapi/middleware/cors.py", "fastapi/applications.py"]),
        ("gzip compression middleware", ["fastapi/middleware/gzip.py", "fastapi/middleware/wsgi.py", "fastapi/applications.py"]),
        ("test client TestClient for apps", ["fastapi/testclient.py", "fastapi/applications.py", "fastapi/routing.py"]),
        ("datastructures UploadFile Form", ["fastapi/datastructures.py", "fastapi/dependencies/utils.py", "fastapi/routing.py"]),
        ("utils generate_unique_id openapi", ["fastapi/utils.py", "fastapi/openapi/utils.py", "fastapi/routing.py"]),
        ("logger configuration for FastAPI", ["fastapi/logger.py", "fastapi/applications.py", "fastapi/__init__.py"]),
        ("types IncEx Json encodable", ["fastapi/types.py", "fastapi/encoders.py", "fastapi/_compat/v2.py"]),
        ("responses JSONResponse HTMLResponse", ["fastapi/responses.py", "fastapi/routing.py", "fastapi/encoders.py"]),
        ("openapi models schema definitions", ["fastapi/openapi/models.py", "fastapi/openapi/utils.py", "fastapi/openapi/constants.py"]),
    ],
    "httpx": [
        ("HTTP client connection pooling", ["httpx/_client.py", "httpx/_transports/default.py", "httpx/_config.py"]),
        ("async client request streaming", ["httpx/_client.py", "httpx/_models.py", "httpx/_transports/asgi.py"]),
        ("authentication BasicAuth DigestAuth", ["httpx/_auth.py", "httpx/_client.py", "httpx/_models.py"]),
        ("URL parsing and query params", ["httpx/_urls.py", "httpx/_utils.py", "httpx/_models.py"]),
        ("HTTP exceptions and status errors", ["httpx/_exceptions.py", "httpx/_status_codes.py", "httpx/_models.py"]),
    ],
    "kubernetes": [
        ("kubelet pod lifecycle sync", ["pkg/kubelet/kubelet.go", "pkg/kubelet/pod_workers.go", "pkg/kubelet/status/status_manager.go"]),
        ("kube-apiserver admission plugins", ["staging/src/k8s.io/apiserver/pkg/admission/plugin.go", "pkg/kubeapiserver/options/admission.go", "cmd/kube-apiserver/app/server.go"]),
        ("scheduler pod scheduling framework", ["pkg/scheduler/scheduler.go", "pkg/scheduler/framework/interface.go", "pkg/scheduler/core/generic_scheduler.go"]),
        ("controller manager shared informers", ["pkg/controller/controller_utils.go", "cmd/kube-controller-manager/app/controllermanager.go", "pkg/controller/client_builder.go"]),
        ("kubectl get pods command", ["staging/src/k8s.io/kubectl/pkg/cmd/get/get.go", "staging/src/k8s.io/cli-runtime/pkg/resource/builder.go", "cmd/kubectl/kubectl.go"]),
        ("deployment rolling update strategy", ["pkg/controller/deployment/deployment_controller.go", "pkg/apis/apps/types.go", "pkg/registry/apps/deployment/storage/storage.go"]),
        ("go module dependencies staging", ["go.mod", "staging/src/k8s.io/api/go.mod", "staging/src/k8s.io/apimachinery/go.mod"]),
    ],
}


def add_concept_cases(repo_key: str, existing_ids: set[str], target: int) -> list[dict]:
    cases: list[dict] = []
    prefix = {
        "fastapi": "fastapi_concept",
        "kubernetes": "k8s_concept",
        "httpx": "httpx_concept",
    }.get(repo_key, f"{repo_key}_concept")
    for idx, (query, expected) in enumerate(CONCEPT_QUERIES.get(repo_key, [])):
        if len(existing_ids) + len(cases) >= target:
            break
        case_id = f"{prefix}_{idx:02d}"
        if case_id in existing_ids:
            continue
        cases.append(
            {
                "case_id": case_id,
                "repo_key": repo_key,
                "seed_files": [],
                "query": query,
                "expected_top_files": expected,
                "tier": 1,
                "token_budget": 8000,
                "max_depth": 1,
                "category": "concept",
                "notes": f"Query-first concept case: {query[:60]}",
            }
        )
        existing_ids.add(case_id)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fastapi-repo", type=Path, default=Path("bench/fastapi"))
    parser.add_argument("--httpx-repo", type=Path, default=Path("bench/httpx"))
    parser.add_argument("--k8s-repo", type=Path, default=Path("bench/kubernetes"))
    parser.add_argument("--fastapi-target", type=int, default=50)
    parser.add_argument("--httpx-target", type=int, default=12)
    parser.add_argument("--k8s-target", type=int, default=20)
    args = parser.parse_args()

    fp = Path("tests/eval/golden/fastapi/cases.json")
    data = json.loads(fp.read_text())
    existing = {c["case_id"] for c in data["cases"]}
    new = gen_cases(args.fastapi_repo, "fastapi", existing, args.fastapi_target, "fastapi")
    data["cases"].extend(new)
    existing = {c["case_id"] for c in data["cases"]}
    concept = add_concept_cases("fastapi", existing, args.fastapi_target)
    data["cases"].extend(concept)
    fp.write_text(json.dumps(data, indent=2) + "\n")
    print(f"fastapi: {len(data['cases'])} cases (+{len(new)} co_change, +{len(concept)} concept)")

    hp = Path("tests/eval/golden/httpx/cases.json")
    hp.parent.mkdir(parents=True, exist_ok=True)
    hdata = json.loads(hp.read_text()) if hp.exists() else {"cases": []}
    hexisting = {c["case_id"] for c in hdata["cases"]}
    hnew = gen_cases(args.httpx_repo, "httpx", hexisting, args.httpx_target, "httpx")
    hdata["cases"].extend(hnew)
    hexisting = {c["case_id"] for c in hdata["cases"]}
    hconcept = add_concept_cases("httpx", hexisting, args.httpx_target)
    hdata["cases"].extend(hconcept)
    hp.write_text(json.dumps(hdata, indent=2) + "\n")
    print(f"httpx: {len(hdata['cases'])} cases (+{len(hnew)} co_change, +{len(hconcept)} concept)")

    kp = Path("tests/eval/golden/kubernetes/cases.json")
    kp.parent.mkdir(parents=True, exist_ok=True)
    kdata = json.loads(kp.read_text()) if kp.exists() else {"cases": []}
    kexisting = {c["case_id"] for c in kdata["cases"]}
    knew = gen_cases(args.k8s_repo, "kubernetes", kexisting, args.k8s_target, "k8s")
    kdata["cases"].extend(knew)
    kexisting = {c["case_id"] for c in kdata["cases"]}
    kconcept = add_concept_cases("kubernetes", kexisting, args.k8s_target)
    kdata["cases"].extend(kconcept)
    kp.write_text(json.dumps(kdata, indent=2) + "\n")
    print(f"kubernetes: {len(kdata['cases'])} cases (+{len(knew)} co_change, +{len(kconcept)} concept)")


if __name__ == "__main__":
    main()
