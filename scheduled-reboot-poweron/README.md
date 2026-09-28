# Scheduled Reboot & Power-On Module

This module defines utilities and schedules for automated power management (power-on & shutdown) for Harvester baremetal nodes and KubeVirt VirtualMachines.

## Architecture

1. **Baremetal Power-On (Physical Server)**:
   - Uses **IPMI over LAN** (`ipmitool -I lanplus`) to send `chassis power on` to the BMC IP of the targeted servers.
   - Operated from the surviving Harvester management node or standalone runner.
   - Monitors Kubernetes node status until the node reaches `Ready`.

2. **Virtual Machine Power-On (KubeVirt)**:
   - Once the baremetal server is `Ready`, the service patches `VirtualMachine` resources to `spec.running: true` (or `spec.runStrategy: Always`).

## Testing IPMI Manually
```bash
# Check power status
ipmitool -I lanplus -H <BMC_IP> -U <USER> -P <PASSWORD> chassis power status

# Power on server
ipmitool -I lanplus -H <BMC_IP> -U <USER> -P <PASSWORD> chassis power on
```
