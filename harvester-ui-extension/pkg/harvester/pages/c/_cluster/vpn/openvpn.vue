<script>
import VpnProviderForm from '../../../../components/VpnProviderForm.vue';

export default {
  name:       'HarvesterVpnOpenvpn',
  components: { VpnProviderForm },

  data() {
    return {
      fields: [
        {
          key:         'config',
          label:       'Client profile (.ovpn)',
          type:        'textarea',
          required:    true,
          placeholder: 'client\ndev tun\nproto udp\nremote vpn.example.com 1194\n...\n<ca>\n-----BEGIN CERTIFICATE-----\n...',
          hint:        'Paste the full .ovpn profile, inline certificates included.',
        },
        {
          key:         'username',
          label:       'Username',
          type:        'text',
          placeholder: 'Only if the profile uses auth-user-pass',
        },
        {
          key:         'password',
          label:       'Password',
          type:        'password',
          placeholder: 'Only if the profile uses auth-user-pass',
        },
      ],
    };
  },
};
</script>

<template>
  <VpnProviderForm
    provider="openvpn"
    title="OpenVPN"
    description="Run an OpenVPN client on every Harvester node. The agent DaemonSet deploys once a client profile is set."
    :fields="fields"
  />
</template>
