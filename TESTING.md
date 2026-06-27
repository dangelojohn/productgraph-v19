# Testing ProductGraph locally

Per CLAUDE.md, nothing goes to the QNAP until base + revision + ebom + release
are green on a local Odoo 19 dev instance. This harness is that local instance.

## Prerequisites

* Docker + `docker compose` (OrbStack, Docker Desktop, or native).
* `odoo:19.0` image (pulled on first `up`).

## Bring up the stack

    docker compose up -d

* Odoo: http://localhost:8069  (master pwd `admin`, DB user `odoo`/`odoo`)
* Postgres: localhost on default port, inside the compose network as `db`.

Compose-mounted dirs:

* `./addons` -> `/mnt/extra-addons` (read-only)
* `./conf/odoo.conf` -> `/etc/odoo/odoo.conf` (read-only)

## Run all tests

    ./scripts/run_tests.sh

Drops + recreates DB `pg_test`, installs every `product_graph_*` addon, runs
`--test-enable` with `--test-tags=/product_graph_*`, stops after init.

## Run tests for one addon

    TEST_TAGS=/product_graph_ebom ./scripts/run_tests.sh

`INSTALL_MODULES` also overridable:

    INSTALL_MODULES=product_graph_base,product_graph_revision \
      TEST_TAGS=/product_graph_revision \
      ./scripts/run_tests.sh

## Custom DB name

    DB_NAME=feature_eco_x ./scripts/run_tests.sh

Lets parallel test DBs coexist without clobbering each other.

## Fresh dev DB for manual exploration

    docker compose exec db createdb -U odoo -O odoo dev
    docker compose exec odoo odoo -d dev \
      -i product_graph_base,product_graph_revision,product_graph_ebom,product_graph_release \
      --without-demo=False --stop-after-init

Then visit http://localhost:8069 and pick `dev`.

## Counter-antipattern lint (PG-107)

    python scripts/lint_counter_antipattern.py

Flags `fields.Integer(compute=..., name='*_count')` declarations without
`store=True` that appear in `<list>`/`<tree>` views. Exits 1 if any found.

## CI

`.github/workflows/tests.yml` runs both the lint and the test harness on
push to `main` and on every PR. Matches the local recipe verbatim.

## Tear down

    docker compose down            # keeps volumes (DB + filestore)
    docker compose down -v         # nuke volumes too (fresh slate)
