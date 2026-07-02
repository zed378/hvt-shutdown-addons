set -e

ENDPOINT="${UI_PLUGIN_ENDPOINT}"
MAX_RETRIES=60
RETRY_INTERVAL=5
RETRY=0

echo "Waiting for UI plugin endpoint to be available..."
echo "Endpoint: ${ENDPOINT}/package.json"

while [ ${RETRY} -lt ${MAX_RETRIES} ]; do
  RETRY=$((RETRY + 1))
  echo "Attempt ${RETRY}/${MAX_RETRIES}..."

  HTTP_CODE=$(curl -s -o /tmp/ui-plugin-response.json -w "%{http_code}" "${ENDPOINT}/package.json" 2>/dev/null || echo "000")

  if [ "${HTTP_CODE}" = "200" ]; then
    echo "Endpoint is available (HTTP ${HTTP_CODE})"

    if grep -q '"rancher"' /tmp/ui-plugin-response.json; then
      echo "Response contains valid Rancher UI plugin annotations"
      break
    else
      echo "Response is not a valid Rancher UI plugin package.json, waiting..."
    fi
  else
    echo "Endpoint not ready (HTTP ${HTTP_CODE}), waiting ${RETRY_INTERVAL}s..."
  fi

  sleep ${RETRY_INTERVAL}
done

if [ ${RETRY} -ge ${MAX_RETRIES} ]; then
  echo "Failed to reach UI plugin endpoint after ${MAX_RETRIES} attempts"
  echo "Please ensure the node-shutdown daemonset is running and the static file server is accessible"
  exit 1
fi

echo "Creating/Updating UIPlugin resource..."

kubectl apply -f - <<EOF
apiVersion: catalog.cattle.io/v1
kind: UIPlugin
metadata:
  name: ${UI_PLUGIN_NAME}
  namespace: ${UI_PLUGIN_NAMESPACE}
spec:
  pluginName: ${UI_PLUGIN_NAME}
  version: "${UI_PLUGIN_VERSION}"
  endpoint: "${ENDPOINT}"
  noCache: ${NO_CACHE}
EOF

echo "UIPlugin resource created/updated successfully"
echo "Rancher will now pick up the UI extension automatically"
