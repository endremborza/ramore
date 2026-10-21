# The `ramore` command on PATH, with the locked dependency versions (`uv tool install` reads no
# lockfile), editable so a pull of this repo is live at once.
install:
	uv export --frozen --no-dev --no-emit-project --no-hashes -o .constraints.txt
	uv tool install --force --editable . --python $$(cat .python-version) --constraints .constraints.txt
	rm .constraints.txt

test:
	uv run pytest -q

check:
	uv run --with ruff ruff format --check .
	uv run --with ruff ruff check .

format:
	uv run --with ruff ruff check --fix .
	uv run --with ruff ruff format .
