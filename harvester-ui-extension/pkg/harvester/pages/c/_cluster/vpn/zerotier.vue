<script>
import VpnProviderForm from '../../../../components/VpnProviderForm.vue';

export default {
  name:       'HarvesterVpnZerotier',
  components: { VpnProviderForm },

  data() {
    return {
      fields: [
        {
          key:         'networkId',
          label:       'Network ID',
          type:        'text',
          required:    true,
          placeholder: '8056c2e21c000001',
          hint:        'The 16-character network ID. Unless the network is public, each node must then be authorized in ZeroTier Central.',
        },
      ],
    };
  },
};
</script>

<template>
  <VpnProviderForm
    provider="zerotier"
    title="ZeroTier"
    description="Join every Harvester node to a ZeroTier network. The agent DaemonSet deploys once a network ID is set."
    :fields="fields"
  />
</template>
