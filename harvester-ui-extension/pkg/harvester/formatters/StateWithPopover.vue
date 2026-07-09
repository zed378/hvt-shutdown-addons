<script>
import State from '@shell/components/formatter/BadgeStateFormatter';

export default {
  components: { State },

  props: {
    row: {
      type:     Object,
      required: true
    },
  },

  computed: {
    warningMessages() {
      return this.row.warningMessages;
    },
  },
};
</script>

<template>
  <span>
    <div class="state">
      <State
        :row="row"
      />
      <v-dropdown
        v-if="warningMessages.length > 0"
        trigger="hover"
        offset="16"
      >
        <span class="tooltip-target">
          <i class="icon icon-warning icon-lg text-warning" />
        </span>

        <template #popper>
          <p
            v-for="(message, index) in warningMessages"
            :key="index"
          >
            {{ index + 1 }}.
            {{ message.text }}
          </p>
        </template>
      </v-dropdown>
    </div>
  </span>
</template>

<style lang="scss" scoped>
.state {
  display: flex;

  .icon-warning {
    margin-top: 2px;
  }
}
</style>
