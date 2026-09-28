{{/*
In-cluster URL of the shutdown API (ClusterIP of the NodePort service).
*/}}
{{- define "node-shutdown.apiUrl" -}}
{{ if .Values.tls.enabled }}https{{ else }}http{{ end }}://{{ .Values.daemonset.name }}-nodeport.{{ .Values.daemonset.namespace }}.svc:{{ .Values.nodePort.port }}
{{- end }}

{{/*
A CronJob that POSTs a JSON payload to the shutdown API with the auth token.
Call with: dict "root" $ "name" <name> "schedule" <cron> "path" "/system/..."
           "payload" <dict> "kind" <label> "target" <what it acts on>
*/}}
{{- define "node-shutdown.triggerCronJob" -}}
{{- $root := .root -}}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ .name }}
  namespace: {{ $root.Values.daemonset.namespace }}
  labels:
    app: node-shutdown
    node-shutdown.harvesterhci.io/schedule: {{ .kind | quote }}
  annotations:
    node-shutdown.harvesterhci.io/target: {{ .target | quote }}
spec:
  schedule: {{ .schedule | quote }}
  {{- with $root.Values.scheduleTimeZone }}
  timeZone: {{ . | quote }}
  {{- end }}
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 2
  failedJobsHistoryLimit: 2
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        metadata:
          labels:
            app: node-shutdown-schedule
            node-shutdown.harvesterhci.io/schedule: {{ .kind | quote }}
        spec:
          serviceAccountName: {{ $root.Values.rbac.serviceAccountName }}
          restartPolicy: Never
          containers:
            - name: trigger
              image: "{{ $root.Values.image.registry }}/{{ $root.Values.image.repository }}:{{ $root.Values.image.tag }}"
              imagePullPolicy: {{ $root.Values.image.pullPolicy }}
              env:
                - name: AUTH_TOKEN
                  valueFrom:
                    secretKeyRef:
                      name: {{ $root.Values.secret.name }}
                      key: {{ $root.Values.secret.key }}
                - name: API_URL
                  value: {{ include "node-shutdown.apiUrl" $root | quote }}
                - name: API_PATH
                  value: {{ .path | quote }}
                - name: PAYLOAD
                  value: {{ .payload | toJson | quote }}
              command: ["python", "-c"]
              args:
                - |
                  import os, ssl, urllib.request
                  url = os.environ["API_URL"].rstrip("/") + os.environ["API_PATH"]
                  ctx = None
                  if url.startswith("https"):
                      ctx = ssl.create_default_context()
                      ctx.check_hostname = False
                      ctx.verify_mode = ssl.CERT_NONE
                  req = urllib.request.Request(
                      url, method="POST", data=os.environ["PAYLOAD"].encode(),
                      headers={"Authorization": "Bearer " + os.environ["AUTH_TOKEN"],
                               "Content-Type": "application/json"})
                  print("POST", os.environ["API_PATH"], os.environ["PAYLOAD"])
                  print(urllib.request.urlopen(req, timeout=30, context=ctx).read().decode())
{{- end }}

{{/*
DNS-safe CronJob name: <prefix>-<readable part>-<short hash>-<suffix>, <= 52 chars.
Call with: list <prefix> <readable> <unique key> <suffix>
*/}}
{{- define "node-shutdown.scheduleName" -}}
{{- $prefix := index . 0 -}}
{{- $readable := index . 1 | lower | replace "/" "-" | replace "." "-" | replace "_" "-" | trunc 24 | trimSuffix "-" -}}
{{- $hash := index . 2 | sha256sum | trunc 6 -}}
{{- printf "%s-%s-%s-%s" $prefix $readable $hash (index . 3) | trunc 52 | trimSuffix "-" -}}
{{- end }}
