<script>
import Members from '@pkg/components/Members';
import { MANAGEMENT } from '@shell/config/types';
import { PRODUCT_NAME as HARVESTER_PRODUCT } from '../../../../config/harvester';
export default {
  components:   { Members },
  inheritAttrs: false,
  computed:     {
    createLocationOverride() {
      return {
        name:   `${ HARVESTER_PRODUCT }-c-cluster-resource-create`,
        params: {
          product:  HARVESTER_PRODUCT,
          cluster:  this.$store.getters['currentCluster']?.id,
          resource: MANAGEMENT.CLUSTER_ROLE_TEMPLATE_BINDING
        }
      };
    }
  }
};
</script>

<template>
  <Members
    :create-location-override="createLocationOverride"
    v-bind="$attrs"
  />
</template>
