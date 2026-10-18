#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$ROOT/restful-booker-main"
OUT_DIR="$ROOT/results"
COMPOSE=(docker compose -f "$PROJECT_DIR/docker-compose.yml" --project-directory "$PROJECT_DIR")

for tool in docker curl python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Не найден $tool. Установите инструмент и повторите запуск." >&2
    exit 2
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo 'Не найден Docker Compose v2 (docker compose).' >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Docker daemon не запущен.' >&2
  exit 2
fi
if [[ ! -f "$PROJECT_DIR/package.json" ]]; then
  echo "Не найден проект Restful-Booker: $PROJECT_DIR" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
cd "$ROOT"
{
  echo "Started (UTC): $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "Platform: $(uname -a)"
  echo "Load generator: grafana/k6:latest in Docker, shared network namespace with API"
  echo "Docker: $(docker --version)"
  echo "Compose: $(docker compose version)"
  echo "API base URL: http://localhost:3001"
  echo 'Scenario: 30s ramp-up + 90s plateau + 30s ramp-down; max VUs 10 and 40'
} > "$OUT_DIR/environment.txt"

"${COMPOSE[@]}" build
k6_version="$(docker run --rm grafana/k6:latest version)"
printf 'k6: %s\n' "$k6_version" >> "$OUT_DIR/environment.txt"

run_case() {
  local run_name="$1"
  local max_vus="$2"
  echo "=== $run_name: MAX_VUS=$max_vus ==="
  "${COMPOSE[@]}" down --remove-orphans
  "${COMPOSE[@]}" up -d --force-recreate

  local ready=0
  for _ in $(seq 1 60); do
    if [[ "$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' http://localhost:3001/ping 2>/dev/null || true)" == 201 ]]; then
      ready=1
      break
    fi
    sleep 2
  done
  if (( ready == 0 )); then
    echo 'Сервис не стал доступен на http://localhost:3001/ping' >&2
    "${COMPOSE[@]}" logs --tail 60 >&2
    return 3
  fi

  curl -fsS -H 'Accept: application/json' http://localhost:3001/booking \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d,list) and len(d)>0; print("Seeded bookings:",len(d))' \
    > "$OUT_DIR/$run_name-preflight.txt"
  cat "$OUT_DIR/$run_name-preflight.txt"

  local exit_code=0
  local container_id
  container_id="$("${COMPOSE[@]}" ps -q restful-booker)"
  if [[ -z "$container_id" ]]; then
    echo "Не найден контейнер API" >&2
    return 4
  fi
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --network "container:$container_id" \
    -v "$ROOT:/work" -w /work \
    grafana/k6:latest run \
    -e BASE_URL=http://localhost:3001 \
    -e MAX_VUS="$max_vus" \
    -e RESULT_FILE="results/$run_name-summary.json" \
    /work/load_test.js 2>&1 | tee "$OUT_DIR/$run_name-console.log" || exit_code=${PIPESTATUS[0]}
  printf '%s\n' "$exit_code" > "$OUT_DIR/$run_name-exit-code.txt"
  if [[ ! -f "$OUT_DIR/$run_name-summary.json" ]]; then
    echo "Отсутствует summary для $run_name; см. лог." >&2
    return 4
  fi
}

run_case baseline 10
run_case increased 40
"${COMPOSE[@]}" down --remove-orphans
python3 "$ROOT/generate_report.py" --root "$ROOT"
echo "Отчёт создан: $ROOT/REPORT.md"
