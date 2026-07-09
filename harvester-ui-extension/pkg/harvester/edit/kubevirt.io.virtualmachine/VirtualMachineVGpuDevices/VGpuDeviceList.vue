<script>
import ResourceTable from '@shell/components/ResourceTable';
import { HCI as HCI_ANNOTATIONS } from '@pkg/harvester/config/labels-annotations';
import { STATE, SIMPLE_NAME } from '@shell/config/table-headers';
import { defaultTableSortGenerationFn } from '@shell/components/ResourceTable.vue';
import { allHash } from '@shell/utils/promise';
import { HCI } from '../../../types';
import FilterBySriov from '../../../components/FilterBySriov';

export default {
  name: 'VGpuDeviceList',

  components: { ResourceTable, FilterBySriov },

  props: {
    schema: {
      type:     Object,
      required: true,
    },

    devices: {
      type:     Array,
      required: true,
    },

  },
  async fetch() {
    const inStore = this.$store.getters['currentProduct'].inStore;
    const hash = { sriovgpus: this.$store.dispatch(`${ inStore }/findAll`, { type: HCI.SR_IOVGPU_DEVICE }) };

    await allHash(hash);
  },

  data() {
    const headers = [
      { ...STATE },
      SIMPLE_NAME,
      {
        name:  'vGPUTypeName',
        label: 'vGPU Type',
        value: 'spec.vGPUTypeName',
        sort:  ['spec.vGPUTypeName']
      },
      {
        name:     'node',
        labelKey: 'tableHeaders.node',
        value:    'spec.nodeName',
        sort:     ['spec.nodeName']
      },
      {
        name:  'address',
        label: 'Address',
        value: 'spec.address',
        sort:  ['spec.address']
      },
    ];

    return {
      headers,
      rows:        [],
      parentSriov: null,
      filterRows:  []
    };
  },

  watch: {
    devices: {
      handler(v) {
        this.rows = v;
        this.filterRows = this.rows;
      },
      immediate: true,
    }
  },

  computed: {
    parentSriovOptions() {
      const inStore = this.$store.getters['currentProduct'].inStore;
      const allSriovs = this.$store.getters[`${ inStore }/all`](HCI.SR_IOVGPU_DEVICE) || [];

      return allSriovs.map((sriov) => {
        return sriov.id;
      });
    },
    parentSriovLabel() {
      return HCI_ANNOTATIONS.PARENT_SRIOV_GPU;
    }
  },

  methods: {
    changeRows(filterRows, parentSriov) {
      this['filterRows'] = filterRows;
      this['parentSriov'] = parentSriov;
    },

    sortGenerationFn() {
      let base = defaultTableSortGenerationFn(this.schema, this.$store);

      if (this.parentSriov) {
        base += this.parentSriov;
      }

      return base;
    },
  }
};
</script>

<template>
  <ResourceTable
    :headers="headers"
    :schema="schema"
    :rows="filterRows"
    :use-query-params-for-simple-filtering="true"
    :sort-generation-fn="sortGenerationFn"
    :rows-per-page="10"
  >
    <template #more-header-middle>
      <FilterBySriov
        ref="filterByParentSRIOV"
        :parent-sriov-options="parentSriovOptions"
        :parent-sriov-label="parentSriovLabel"
        :label="t('harvester.sriovgpu.parentSriov')"
        :rows="rows"
        @change-rows="changeRows"
      />
    </template>
  </ResourceTable>
</template>
