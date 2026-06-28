IMAGE   ?= pareto-context-graph:latest
DOCKER  ?= docker
NAME    ?= pareto-context-graph

BENCH_DIR ?= $(CURDIR)/bench
T1_REPOS  ?= fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx
BASELINE  ?= tests/eval/baseline.json
COMPRESS_BASELINE ?= tests/eval/baseline-compress.json
AGENT_MODEL ?= sonnet
N_RUNS ?= 4
PCG     ?= $(shell if [ -x "$(CURDIR)/.venv/bin/pareto-context-graph" ]; then echo "$(CURDIR)/.venv/bin/pareto-context-graph"; else echo pareto-context-graph; fi)
PYTHON    ?= $(shell if [ -x "$(CURDIR)/.venv/bin/python" ]; then echo "$(CURDIR)/.venv/bin/python"; else echo python3; fi)

.PHONY: help deploy build-image build-graph serve eval eval-baseline eval-check eval-ablation eval-compress-baseline eval-compress-check eval-check-kubernetes eval-audit eval-audit-kubernetes eval-agent-ab-baseline eval-agent-ab-check agent-bench agent-bench-gate memory-probe profile-build render-diagrams bench-setup-t1 bench-setup bench-huge bench-linux bench-smoke bench-stress pre-bench

help:
	@echo "Targets:"
	@echo "  make build-image    Build Docker image ($(IMAGE))"
	@echo "  make build-graph    Build MCP graph in container for current repo"
	@echo "  make serve          Start MCP server via Docker (stdio)"
	@echo "  make deploy         Build image + build graph + start server"
	@echo "  make eval           Run eval (REPOS=fastapi=$(BENCH_DIR)/fastapi)"
	@echo "  make eval-baseline  Refresh tests/eval/baseline.json (BASELINE= to override)"
	@echo "  make eval-check     Fail if metrics regress vs baseline (default T1: fastapi + httpx)"
	@echo "  make eval-ablation  Print per-signal ablation table (pool / pre-MMR / recall@5)"
	@echo "  make eval-compress-check  Phase C: recall + tier-3 compression gates"
	@echo "  make eval-compress-baseline  Refresh tests/eval/baseline-compress.json"
	@echo "  make eval-agent-ab-baseline  Refresh tests/eval/baseline-agent-ab.json"
	@echo "  make eval-agent-ab-check     Agent A/B gate (tool calls + recall vs baseline)"
	@echo "  make profile-build  Show or run build phase profile (REPO=, SHOW=1, REPLAY=1)"
	@echo "  make render-diagrams Regenerate docs/diagrams/*.svg from .mmd sources"
	@echo "  make bench-setup-t1 Phase 0: clone + build fastapi + httpx"
	@echo "  make bench-setup    Phase 0: clone + build + pin SHAs (TIER=1|2|3|all)"
	@echo "                      Skip clone: make bench-setup TIER=2 SKIP_CLONE=1"
	@echo "  make bench-huge     Tier 2/3 stress (REPOS=key=$(BENCH_DIR)/path)"
	@echo "  make bench-linux    Tier 3: clone + build + bench torvalds/linux"
	@echo "  make pre-bench      CI gate before bench-linux (ruff, mypy, pytest, eval)"
	@echo "  make bench-smoke    stats + doctor on T1 clones"
	@echo "  make bench-stress   Phase 6 synthetic huge-profile CI gate"

build-image:
	$(DOCKER) build -t $(IMAGE) .

build-graph: build-image
	$(DOCKER) rm -f $(NAME)-build 2>/dev/null || true
	$(DOCKER) run --rm --name $(NAME)-build -v "$(PWD):/workspace" $(IMAGE) --repo /workspace build

serve: build-image
	$(DOCKER) rm -f $(NAME) 2>/dev/null || true
	$(DOCKER) run --rm -i --name $(NAME) -v "$(PWD):/workspace" $(IMAGE)

deploy: build-image build-graph serve

eval:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval REPOS=fastapi=$(BENCH_DIR)/fastapi"; exit 1; fi
	$(PCG) eval $(foreach r,$(REPOS),--repo-map $(r))

eval-baseline:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval-baseline REPOS='fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx'"; exit 1; fi
	$(PCG) eval $(foreach r,$(REPOS),--repo-map $(r)) --baseline $(BASELINE) --update-baseline

eval-check:
	$(PCG) eval $(foreach r,$(if $(REPOS),$(REPOS),$(T1_REPOS)),--repo-map $(r)) --baseline $(BASELINE) --check-baseline

eval-ablation:
	@export PCG_EDGE_DECAY=0; \
	$(PCG) eval $(foreach r,$(if $(REPOS),$(REPOS),$(T1_REPOS)),--repo-map $(r)) --ablation

eval-compress-baseline:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval-compress-baseline REPOS='fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx'"; exit 1; fi
	$(PCG) eval $(foreach r,$(REPOS),--repo-map $(r)) --compress-stack --compress-baseline $(COMPRESS_BASELINE) --update-compress-baseline

eval-compress-check:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval-compress-check REPOS='fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx'"; exit 1; fi
	$(PCG) eval $(foreach r,$(REPOS),--repo-map $(r)) --compress-stack --baseline $(BASELINE) --check-baseline --compress-baseline $(COMPRESS_BASELINE) --check-compress-baseline

eval-check-kubernetes:
	$(MAKE) eval-check REPOS='kubernetes=$(BENCH_DIR)/kubernetes' BASELINE=tests/eval/baseline-kubernetes.json

eval-audit-kubernetes:
	$(MAKE) eval-audit REPOS='kubernetes=$(BENCH_DIR)/kubernetes'

eval-pr-cases:
	@if [ -z "$(REPO)" ]; then echo "Usage: make eval-pr-cases REPO=$(BENCH_DIR)/fastapi"; exit 1; fi
	PYTHONPATH=. $(PYTHON) scripts/expand_golden_from_prs.py --repo $(REPO) --min-new 10

profile-build:
	@if [ -z "$(REPO)" ]; then echo "Usage: make profile-build REPO=$(BENCH_DIR)/kubernetes [SHOW=1|REPLAY=1|BUILD=1]"; exit 1; fi
	@if [ -n "$(SHOW)" ]; then PYTHONPATH=. $(PYTHON) scripts/profile_build.py --repo $(REPO) --show; \
	elif [ -n "$(REPLAY)" ]; then PYTHONPATH=. $(PYTHON) scripts/profile_build.py --repo $(REPO) --replay-index; \
	elif [ -n "$(BUILD)" ]; then PYTHONPATH=. $(PYTHON) scripts/profile_build.py --repo $(REPO) --build --commits $(or $(COMMITS),5000) --shards $(or $(SHARDS),1) $(if $(SINCE),--since '$(SINCE)',); \
	else PYTHONPATH=. $(PYTHON) scripts/profile_build.py --repo $(REPO) --show; fi

render-diagrams:
	./scripts/render_diagrams.sh

AGENT_AB_BASELINE ?= tests/eval/baseline-agent-ab.json

eval-agent-ab-baseline:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval-agent-ab-baseline REPOS='fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx'"; exit 1; fi
	$(PCG) eval $(foreach r,$(REPOS),--repo-map $(r)) --agent-ab --agent-ab-baseline $(AGENT_AB_BASELINE) --update-agent-ab-baseline

eval-agent-ab-check:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval-agent-ab-check REPOS='fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx'"; exit 1; fi
	$(PCG) eval $(foreach r,$(REPOS),--repo-map $(r)) --agent-ab --agent-ab-baseline $(AGENT_AB_BASELINE) --check-agent-ab

eval-audit:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval-audit REPOS='fastapi=$(BENCH_DIR)/fastapi httpx=$(BENCH_DIR)/httpx'"; exit 1; fi
	PYTHONPATH=. $(PYTHON) scripts/audit_golden_cases.py $(foreach r,$(REPOS),--repo-map $(r))

# Phase 1.3/1.5 — real agent A/B from claude -p transcripts (needs `claude` CLI + jq).
# agent-bench runs the live arms; agent-bench-gate fails if pcg loses to baseline.
agent-bench:
	N_RUNS="$(N_RUNS)" ./scripts/agent_bench.sh $(N_RUNS) $(AGENT_MODEL)

agent-bench-gate:
	PYTHONPATH=. $(PYTHON) scripts/agent_ab_check.py tests/eval/agent-ab.json

memory-probe:
	./scripts/memory_probe.sh $(AGENT_MODEL)

bench-setup-t1:
	BENCH_DIR="$(BENCH_DIR)" PCG="$(PCG)" ./scripts/bench_setup.sh --tier 1

bench-setup:
	BENCH_DIR="$(BENCH_DIR)" PCG="$(PCG)" ./scripts/bench_setup.sh --tier $(or $(TIER),1) --update-pins $(if $(filter 1,$(SKIP_CLONE)),--skip-clone,)

bench-huge:
	@if [ -z "$(REPOS)" ]; then echo "Usage: make bench-huge REPOS=kubernetes=$(BENCH_DIR)/kubernetes"; exit 1; fi
	PCG="$(PCG)" ./scripts/bench_huge.sh $(REPOS)

bench-linux:
	BENCH_DIR="$(BENCH_DIR)" PCG="$(PCG)" ./scripts/linux_bench.sh

pre-bench:
	BENCH_DIR="$(BENCH_DIR)" PCG="$(PCG)" ./scripts/pre_bench.sh

bench-smoke:
	@for key in fastapi httpx; do \
	  if [ -d "$(BENCH_DIR)/$$key/.git" ]; then \
	    echo "=== $$key ==="; \
	    (cd "$(BENCH_DIR)/$$key" && "$(PCG)" stats && "$(PCG)" doctor); \
	  else \
	    echo "skip $$key (run make bench-setup-t1 first)"; \
	  fi; \
	done

bench-stress:
	PYTHONPATH=. $(PYTHON) -m pytest -q tests/test_bench_stress.py
	PYTHONPATH=. $(PYTHON) tests/perf/bench_build.py
	PYTHONPATH=. $(PYTHON) tests/perf/bench_query.py
