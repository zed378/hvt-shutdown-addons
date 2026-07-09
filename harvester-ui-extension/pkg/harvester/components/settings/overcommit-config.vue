<script>
import CreateEditView from '@shell/mixins/create-edit-view';
import UnitInput from '@shell/components/form/UnitInput';

export default {
  name: 'HarvesterOvercommitConfig',

  components: { UnitInput },

  mixins: [CreateEditView],

  data() {
    let parseDefaultValue = {};

    try {
      parseDefaultValue = JSON.parse(this.value.value);
    } catch (error) {
      parseDefaultValue = JSON.parse(this.value.default);
    }

    return {
      parseDefaultValue,
      errors: []
    };
  },

  created() {
    this.update();
  },

  methods: {
    update() {
      const value = JSON.stringify(this.parseDefaultValue);

      this.value['value'] = value;
    }
  },

  watch: {
    value: {
      handler(neu) {
        const parseDefaultValue = JSON.parse(neu.value);

        this['parseDefaultValue'] = parseDefaultValue;
      },
      deep: true
    }
  }
};
</script>

<template>
  <div class="row">
    <div class="col span-12">
      <UnitInput
        v-model:value="parseDefaultValue.cpu"
        label-key="harvester.generic.cpu"
        suffix="%"
        :delay="0"
        required
        :mode="mode"
        class="mb-20"
        @update:value="update"
      />
      <UnitInput
        v-model:value="parseDefaultValue.memory"
        label-key="harvester.generic.memory"
        suffix="%"
        :delay="0"
        required
        :mode="mode"
        class="mb-20"
        @update:value="update"
      />
      <UnitInput
        v-model:value="parseDefaultValue.storage"
        label-key="harvester.generic.storage"
        suffix="%"
        :delay="0"
        required
        :mode="mode"
        class="mb-20"
        @update:value="update"
      />
    </div>
  </div>
</template>
