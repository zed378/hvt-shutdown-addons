<script>
import SSHKey from '../../VirtualMachineSSHKey';
import AccessCredentialsUsers from '../AccessCredentialsUsers';

export default {
  name: 'HarvesterEditVolume',

  emits: ['update', 'update:newUser'],

  components: { SSHKey, AccessCredentialsUsers },

  props: {
    mode: {
      type:    String,
      default: 'create'
    },

    resourceType: {
      type:    Object,
      default: () => {
        return {};
      }
    },

    userOptions: {
      type:    Array,
      default: () => {
        return [];
      }
    },

    value: {
      type:    Object,
      default: () => {
        return {};
      }
    },
  },

  methods: {
    update() {
      this.$emit('update');
    },

    updateUser(neu) {
      this.value['users'] = neu;
      this.update();
    },

    updateSSH(neu) {
      this.value['sshkeys'] = neu;
      this.update();
    },

    updateNewUser(neu) {
      this.$emit('update:newUser', neu);
    }
  },
};
</script>

<template>
  <div>
    <div class="columns row">
      <div class="col span-6">
        <AccessCredentialsUsers
          v-model:value="value.users"
          :resource-type="resourceType"
          :user-options="userOptions"
          :mode="mode"
          :multiple="true"
          @update:user="updateUser"
          @update:newUser="updateNewUser"
        />
      </div>
      <div class="col span-6">
        <SSHKey
          v-model:value="value.sshkeys"
          class="mb-20"
          :namespace="resourceType.metadata.namespace"
          :mode="mode"
          :searchable="false"
          @update:sshKey="updateSSH"
        />
      </div>
    </div>
  </div>
</template>
