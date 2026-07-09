<script>
import CreateEditView from '@shell/mixins/create-edit-view';
import { LabeledInput } from '@components/Form/LabeledInput';

export default {
  name: 'AdditionalGuestMemoryOverheadRatio',

  components: { LabeledInput },

  mixins: [CreateEditView],

  data() {
    return { ratio: this.value.value || this.value.default };
  },

  methods: {
    update() {
      this.value['value'] = this.ratio;
    },

    useDefault() {
      this['ratio'] = this.value.default;
      this.update();
    },
  },
};
</script>

<template>
  <div class="row">
    <div class="col span-12">
      <LabeledInput
        v-model:value="ratio"
        :label="t('harvester.setting.ratio')"
        @update:value="update"
      />
    </div>
  </div>
</template>
