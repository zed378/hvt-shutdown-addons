<script>
import CreateEditView from '@shell/mixins/create-edit-view';
import { RadioGroup } from '@components/Form/Radio';
import UnitInput from '@shell/components/form/UnitInput';

export default {
  name: 'HarvesterAutoRotateRKE2Certs',

  components: { RadioGroup, UnitInput },

  mixins: [CreateEditView],

  data() {
    let parseDefaultValue = {};

    try {
      parseDefaultValue = JSON.parse(this.value.value);
    } catch (error) {
      parseDefaultValue = JSON.parse(this.value.default);
    }

    return { parseDefaultValue };
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
      <RadioGroup
        v-model:value="parseDefaultValue.enable"
        class="mb-20"
        name="model"
        :options="[true,false]"
        :labels="[t('generic.enabled'), t('generic.disabled')]"
        @update:value="update"
      />
      <UnitInput
        v-if="parseDefaultValue.enable"
        v-model:value.number="parseDefaultValue.expiringInHours"
        class="mb-20"
        :min="1"
        :max="8759"
        :required="true"
        :suffix="parseDefaultValue.expiringInHours === 1 ? 'Hour' : 'Hours'"
        :label="t('harvester.setting.autoRotateRKE2Certs.expiringInHours')"
        :mode="mode"
        @update:value="update"
      />
    </div>
  </div>
</template>

<style lang="scss" scoped>
  :deep() .radio-group {
    display: flex;
    .radio-container {
      margin-right: 30px;
    }
  }
</style>
