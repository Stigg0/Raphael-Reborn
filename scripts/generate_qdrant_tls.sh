#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT_DIR}/certs/qdrant"
OPENSSL_CNF="${CERT_DIR}/openssl.cnf"

mkdir -p "${CERT_DIR}"

cat > "${OPENSSL_CNF}" <<'EOF'
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = req_ext

[dn]
CN = qdrant

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = qdrant
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

openssl genrsa -out "${CERT_DIR}/ca-key.pem" 4096
openssl req -x509 -new -nodes \
  -key "${CERT_DIR}/ca-key.pem" \
  -sha256 \
  -days 3650 \
  -out "${CERT_DIR}/ca.pem" \
  -subj "/CN=Raphael Reborn Local Qdrant CA"

openssl genrsa -out "${CERT_DIR}/server-key.pem" 4096
openssl req -new \
  -key "${CERT_DIR}/server-key.pem" \
  -out "${CERT_DIR}/server.csr" \
  -config "${OPENSSL_CNF}"

openssl x509 -req \
  -in "${CERT_DIR}/server.csr" \
  -CA "${CERT_DIR}/ca.pem" \
  -CAkey "${CERT_DIR}/ca-key.pem" \
  -CAcreateserial \
  -out "${CERT_DIR}/server.pem" \
  -days 825 \
  -sha256 \
  -extensions req_ext \
  -extfile "${OPENSSL_CNF}"

rm -f "${CERT_DIR}/server.csr"
chmod 600 "${CERT_DIR}/ca-key.pem"
# Mounted into non-root containers; keep cert/key readable inside the user namespace.
chmod 644 "${CERT_DIR}/ca.pem" "${CERT_DIR}/server.pem" "${CERT_DIR}/server-key.pem"

echo "Generated Qdrant TLS material in ${CERT_DIR}"
