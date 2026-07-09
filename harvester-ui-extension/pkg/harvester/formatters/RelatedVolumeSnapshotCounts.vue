<script>
export default {
  props: {
    value: {
      type:    Array,
      default: () => {
        return [];
      }
    },
    row: {
      type:    Object,
      default: () => {
        return {};
      }
    }
  },

  computed: {
    to() {
      const to = this.row?.detailLocation;

      to.hash = '#instances';

      return to;
    },
  }
};
</script>

<template>
  <router-link
    v-if="value.length > 0"
    :to="to"
  >
    {{ value.length }}
  </router-link>
</template>
