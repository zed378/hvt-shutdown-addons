{{/*
Shared scaffolding for the VPN agent DaemonSets. Every provider needs the same
three things, so they live here rather than being repeated four times:

  * hostNetwork — the agent manages an interface on the NODE, not in a pod netns.
  * ClusterFirstWithHostNet — keep cluster DNS working despite hostNetwork.
  * tolerations: Exists — a node-level VPN belongs on EVERY node, including
    control-plane/tainted ones. Skipping a tainted node would silently leave it
    off the VPN.
*/}}
{{- define "vpn.agentPodBase" -}}
hostNetwork: true
dnsPolicy: ClusterFirstWithHostNet
tolerations:
  - operator: Exists
{{- end -}}

{{/*
/dev/net/tun, needed by every one of these agents to create a tunnel interface.
*/}}
{{- define "vpn.tunVolume" -}}
- name: tun
  hostPath:
    path: /dev/net/tun
{{- end -}}

{{- define "vpn.tunMount" -}}
- name: tun
  mountPath: /dev/net/tun
{{- end -}}
