#!/usr/bin/env bash
# Run the existing static HTTP/HTTPS fixtures on the GitHub Actions runner.
set -euo pipefail
image=${1:?Usage: test_image.sh IMAGE}
work=$(mktemp -d)
diag="${RUNNER_TEMP:-/tmp}/nginx-diagnostics"
mkdir -p "$diag"
trap 'rm -rf "$work"' EXIT

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -subj /CN=localhost -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$work/localhost.key" -out "$work/localhost.crt" > "$diag/certificate-generation.log" 2>&1

docker run -d --name test_nginx \
  -p 127.0.0.1:8888:80 -p 127.0.0.1:8889:443 \
  -v "$PWD/tests:/static:ro" \
  -v "$PWD/tests/modules.conf:/etc/nginx/main.d/modules.conf:ro" \
  -v "$PWD/tests/static.conf:/etc/nginx/conf.d/static.conf:ro" \
  -v "$PWD/tests/https.conf:/etc/nginx/conf.d/https.conf:ro" \
  -v "$work/localhost.crt:/etc/nginx/ssl/localhost.crt:ro" \
  -v "$work/localhost.key:/etc/nginx/ssl/localhost.key:ro" \
  "$image"

ready=false
for _ in {1..30}; do
  [[ "$(docker inspect --format '{{.State.Running}}' test_nginx)" == true ]] || { docker logs test_nginx; exit 1; }
  if curl --noproxy '*' --silent --fail --max-time 2 http://localhost:8888/ > /dev/null; then
    ready=true
    break
  fi
  sleep 1
done
$ready || { echo 'Nginx did not become ready'; exit 1; }
docker exec test_nginx nginx -t

request() {
  local name=$1 url=$2 protocol=$3
  shift 3
  local result
  result=$(curl --noproxy '*' --silent --show-error --fail-with-body \
    --connect-timeout 3 --max-time 10 --compressed --cacert "$work/localhost.crt" \
    --dump-header "$diag/$name.headers" --output "$diag/$name.body" \
    --write-out '%{http_code} %{http_version}' "$@" "$url")
  [[ "$result" == "200 $protocol" ]] || { echo "Unexpected response: $result"; exit 1; }
  if grep -Eiq '^server[[:space:]]*:' "$diag/$name.headers"; then
    echo 'Server response header must be absent' >&2
    exit 1
  fi
  grep -Fq '<p>It works!</p>' "$diag/$name.body"
}

request http http://localhost:8888/ 1.1 --http1.1 -H 'Accept-Encoding: br'
grep -Eiq '^content-encoding:[[:space:]]*br[[:space:]]*$' "$diag/http.headers"
request https https://localhost:8889/ 2 --http2
# The fixtures permit both versions; explicitly verify both supported TLS handshakes.
request tls12 https://localhost:8889/ 2 --http2 --tlsv1.2 --tls-max 1.2
request tls13 https://localhost:8889/ 2 --http2 --tlsv1.3 --tls-max 1.3
echo 'PASS HTTP status/body, Brotli, Server header removal, HTTP/2 and TLS 1.2/1.3'
