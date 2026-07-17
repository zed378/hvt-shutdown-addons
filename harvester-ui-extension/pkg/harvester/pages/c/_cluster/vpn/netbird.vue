<script>
import VpnProviderForm from '../../../../components/VpnProviderForm.vue';

export default {
  name:       'HarvesterVpnNetbird',
  components: { VpnProviderForm },

  data() {
    return {
      fields: [
        {
          key:         'managementUrl',
          label:       'Management URL (domain)',
          type:        'text',
          required:    true,
          placeholder: 'https://api.netbird.io',
          hint:        'The NetBird cloud URL, or your self-hosted management URL.',
        },
        {
          key:         'setupKey',
          label:       'Setup key',
          type:        'password',
          required:    true,
          placeholder: 'Reusable setup key',
          hint:        'Use a reusable key — every node enrolls with the same one.',
        },
      ],
    };
  },
};
</script>

<template>
  <VpnProviderForm
    provider="netbird"
    title="NetBird"
    description="Enroll every Harvester node into your NetBird network. The agent DaemonSet deploys once a management URL and setup key are set."
    :fields="fields"
  />
</template>
