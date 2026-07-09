<script>
import Password from '@shell/components/form/Password';
import AccessCredentialsUsers from '../AccessCredentialsUsers';

export default {
  name: 'HarvesterEditVolume',

  emits: ['update', 'update:newUser'],

  components: { Password, AccessCredentialsUsers },

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

  data() {
    return {
      defaultPwdValue: {
        username:    '',
        newPassword: ''
      }
    };
  },

  methods: {
    update() {
      this.$emit('update');
    },

    updateUser(neu) {
      this.value['username'] = neu;
      this.update();
    },

    updateNewUser(neu) {
      this.$emit('update:newUser', neu);
    }
  },
};
</script>

<template>
  <div
    class="row"
    @update:value="update"
  >
    <div class="col span-6">
      <AccessCredentialsUsers
        v-model:value="value.username"
        :resource-type="resourceType"
        :user-options="userOptions"
        :multiple="false"
        :mode="mode"
        @update:user="updateUser"
        @update:newUser="updateNewUser"
      />
    </div>
    <div class="col span-6">
      <Password
        ref="password"
        v-model:value="value.newPassword"
        :mode="mode"
        :disabled="mode !== 'edit'"
        :label="t('harvester.virtualMachine.input.password')"
        required
      />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.action {
  display: flex;
  flex-direction: row-reverse;
}
</style>
