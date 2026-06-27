#!/usr/bin/env bash
# ProductGraph local test harness.
# Brings up docker-compose stack (db + odoo:19.0), drops + recreates a clean
# test DB, and runs --test-enable across all installed product_graph addons.
#
# Usage:
#   ./scripts/run_tests.sh                         # default: all addons
#   TEST_TAGS=/product_graph_ebom ./scripts/run_tests.sh  # one addon
#   DB_NAME=my_test ./scripts/run_tests.sh         # custom DB name
set -euo pipefail

cd "$(dirname "$0")/.."

DB_NAME="${DB_NAME:-pg_test}"
INSTALL_MODULES="${INSTALL_MODULES:-product_graph_base,product_graph_revision,product_graph_ebom,product_graph_release,product_graph_api,product_graph_eco,product_graph_vendor,product_graph_rfq,product_graph_cad,product_graph_relationship,product_graph_procurement,product_graph_eco_relationship}"
TEST_TAGS="${TEST_TAGS:-/product_graph_base,/product_graph_revision,/product_graph_ebom,/product_graph_release,/product_graph_api,/product_graph_eco,/product_graph_vendor,/product_graph_rfq,/product_graph_cad,/product_graph_relationship,/product_graph_procurement,/product_graph_eco_relationship}"

echo "==> bringing up db + odoo"
docker compose up -d db odoo

echo "==> waiting for postgres to accept connections"
for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U odoo >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

echo "==> dropping + recreating ${DB_NAME}"
docker compose exec -T db dropdb -U odoo --if-exists --force "$DB_NAME"
docker compose exec -T db createdb -U odoo -O odoo "$DB_NAME"

echo "==> running tests (install ${INSTALL_MODULES}, tags ${TEST_TAGS})"
docker compose exec -T odoo odoo \
    -d "$DB_NAME" \
    -i "$INSTALL_MODULES" \
    --test-enable \
    --test-tags="$TEST_TAGS" \
    --stop-after-init \
    --without-demo=False \
    --http-port=18069 \
    --log-level=test

echo "==> tests complete. Review output above for FAIL/ERROR lines."
