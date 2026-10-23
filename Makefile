.PHONY: test demo serve catalog impact export audit clean

test:
	PYTHONPATH=src python3 -m pytest -q

demo:
	python3 examples/run_demo.py

serve:
	data-governance-lineage serve --port 8000

catalog:
	data-governance-lineage catalog

impact:
	data-governance-lineage impact orders.raw.order_events

export:
	data-governance-lineage export --format mermaid

audit:
	data-governance-lineage audit --limit 20

clean:
	rm -rf .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
