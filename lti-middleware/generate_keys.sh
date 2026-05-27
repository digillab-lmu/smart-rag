#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Generate RSA keypair for LTI 1.3
# ═════════════════════════════════════════════════════════════════════════════
#
# Run this ONCE when first setting up the LTI middleware.
# The private key signs JWTs sent to the LMS; the public key is given to the
# LMS admin during tool registration.
#
# Usage:  cd lti-middleware/  &&  ./generate_keys.sh
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

mkdir -p ./config

if [[ -f ./config/private.key ]]; then
    echo "ERROR: ./config/private.key already exists. Refusing to overwrite."
    echo "       Delete it first if you really want to regenerate."
    exit 1
fi

openssl genrsa -out ./config/private.key 4096
openssl rsa -in ./config/private.key -pubout -out ./config/public.key

chmod 600 ./config/private.key
chmod 644 ./config/public.key

echo
echo "═══ Keys generated ═════════════════════════════════════════════════"
echo "  ./config/private.key   ← DO NOT commit, DO NOT share"
echo "  ./config/public.key    ← give this to your LMS admin"
echo
echo "Public key (paste into LMS tool registration):"
echo "─────────────────────────────────────────────────────────────────────"
cat ./config/public.key
echo "─────────────────────────────────────────────────────────────────────"
