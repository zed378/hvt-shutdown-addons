<script>
import VpnProviderForm from '../../../../components/VpnProviderForm.vue';

export default {
  name:       'HarvesterVpnTailscale',
  components: { VpnProviderForm },

  data() {
    return {
      fields: [
        {
          key:         'authKey',
          label:       'Auth key',
          type:        'password',
          required:    true,
          placeholder: 'tskey-auth-...',
          hint:        'Use a reusable auth key — every node enrolls with the same one.',
        },
        {
          key:         'loginServer',
          label:       'Login server',
          type:        'text',
          placeholder: 'https://headscale.example.com  (leave empty for Tailscale)',
          hint:        'Only needed to self-host with Headscale.',
        },
        {
          key:         'advertiseRoutes',
          label:       'Advertise routes',
          type:        'text',
          placeholder: '10.0.0.0/24,10.1.0.0/24',
          hint:        'Subnets to advertise from each node. Still needs approval in the admin console.',
        },
        {
          key:   'acceptRoutes',
          label: 'Accept routes advertised by other nodes',
          type:  'checkbox',
        },
        {
          key:   'acceptDns',
          label: 'Accept tailnet DNS (MagicDNS)',
          type:  'checkbox',
          hint:  'Off by default — replacing a node\'s resolv.conf can break cluster DNS.',
        },
      ],
    };
  },
};
</script>

<template>
  <VpnProviderForm
    provider="tailscale"
    title="Tailscale"
    description="Join every Harvester node to your tailnet. The agent DaemonSet deploys once an auth key is set."
    :fields="fields"
  />
</template>
