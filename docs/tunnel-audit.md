# Tunnel trust boundary (AUD-08)

Use Docker Compose 2.24.4+:

    docker compose -f compose.yaml -f compose.tunnel.yaml --profile tunnel up -d

Configure the Cloudflare origin as http://stt:8000. The override removes
host port publication and enables STT_TRUST_PROXY_HEADERS. Do not attach
untrusted containers to the Compose network or publish the STT origin.
Without the override leave proxy trust disabled. Verify rendered Compose
configuration and ingress isolation before deployment.

Token issuance is limited by application ID; failed authentication is
limited independently by the validated proxy IP. Limits are process-local.
