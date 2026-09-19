#!/usr/bin/env bash
# Terraform via Docker.
# Pourquoi : sous Windows, un antivirus qui intercepte le TLS (Avast) casse la
# communication chiffrée entre Terraform et ses providers ; dans un conteneur,
# ce trafic reste interne. Le CA de l'antivirus est ajouté au bundle du conteneur
# pour les appels sortants (registry, AWS, checkip).
#
# Usage : bash infra/terraform/tf.sh init | plan | apply | destroy | output ...
set -euo pipefail
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cygpath -w "$HERE" 2>/dev/null || echo "$HERE")"
AWS_DIR="$(cygpath -w "$HOME/.aws" 2>/dev/null || echo "$HOME/.aws")"

ARGS=(--rm -v "$TF_DIR:/tf" -w /tf -v "$AWS_DIR:/root/.aws:ro" -e AWS_PROFILE="${AWS_PROFILE:-default}")
[[ -t 0 ]] && ARGS+=(-it)

# CA d'interception TLS de l'antivirus (si présent) → bundle combiné dans le conteneur
EXTRA_CA="/c/ProgramData/Avast Software/Avast/wscert.pem"
PRE=""
if [[ -f "$EXTRA_CA" ]]; then
  ARGS+=(-v "$(cygpath -w "$EXTRA_CA"):/tmp/extra-ca.pem:ro")
  PRE='cat /etc/ssl/certs/ca-certificates.crt /tmp/extra-ca.pem > /tmp/ca.pem && export SSL_CERT_FILE=/tmp/ca.pem AWS_CA_BUNDLE=/tmp/ca.pem && '
fi

docker run "${ARGS[@]}" --entrypoint sh hashicorp/terraform:1.9 -c "${PRE}exec terraform $*"
