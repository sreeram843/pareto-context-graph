IMAGE   ?= code-graph-mcp:latest
DOCKER  ?= docker
NAME    ?= code-graph-mcp

.PHONY: help deploy build-image build-graph serve eval

help:
	@echo "Targets:"
	@echo "  make build-image  Build Docker image ($(IMAGE))"
	@echo "  make build-graph  Build MCP graph in container for current repo"
	@echo "  make serve        Start MCP server via Docker (stdio)"
	@echo "  make deploy       Build image + build graph + start server"
	@echo "  make eval         Run eval harness on all repos (pass REPOS=key=/path)"

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
	@if [ -z "$(REPOS)" ]; then echo "Usage: make eval REPOS=key=/path [or REPOS=telapp=/path/to/telapp ...]"; exit 1; fi
	python3 -m code_graph_mcp.eval $(REPOS)
