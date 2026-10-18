BACKEND := backend
# Use the project-local Node (see README) when present.
export PATH := $(CURDIR)/.node/bin:$(PATH)
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin/python
HAS_NPM := $(shell [ -x .node/bin/npm ] && echo yes || command -v npm 2>/dev/null)

.PHONY: setup dev seed test lint eval demo-reset keys deck-numbers node cantonese-review

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r $(BACKEND)/requirements.txt
	@[ -f .env ] || cp .env.example .env
ifdef HAS_NPM
	cd frontend && npm install
else
	@echo "WARNING: npm not found; skipping frontend install. Run `make node` (project-local Node) or install Node 20+, then `make setup` again."
endif

dev:
	@echo "Backend http://localhost:8000  Frontend http://localhost:5173"
	@trap 'kill 0' INT TERM EXIT; \
	(cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000) & \
	(cd frontend && npm run dev) & \
	wait

node:  ## download an official Node LTS into ./.node (no system changes)
	$(PY) -m pip install -q nodeenv
	$(VENV)/bin/nodeenv --node=lts --prebuilt .node

test:
	cd $(BACKEND) && .venv/bin/pytest -q

lint:
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/black --check .
	cd frontend && npx tsc --noEmit -p .

keys:
	@$(PY) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

seed demo-reset:
	cd $(BACKEND) && .venv/bin/python -m app.seed.seed

eval:  ## offline evaluation: red flags, extraction, triage, STT clips, latency -> backend/eval/results.md
	cd $(BACKEND) && .venv/bin/python -m eval.report

deck-numbers:  ## the numbers the pitch deck needs -> backend/eval/deck_numbers.md (never invents a value)
	cd $(BACKEND) && .venv/bin/python -m eval.deck_numbers > /dev/null && echo 'wrote backend/eval/deck_numbers.md'

cantonese-review:  ## regenerate docs/cantonese_review.md for the native-speaker reviewer
	cd $(BACKEND) && .venv/bin/python -m scripts.cantonese_review
