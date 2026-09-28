<script>
import { cronError } from './cron';

export default {
  name: 'VmScheduleRow',

  // Declared so Vue 3 does not also bind the parent's listener as a native event.
  emits: ['update'],

  props: {
    // { vm, enabled, shutdownCron, poweronCron, status, missing }
    value: {
      type:     Object,
      required: true,
    },
  },

  computed: {
    shutdownError() {
      return cronError(this.value.shutdownCron);
    },
    poweronError() {
      return cronError(this.value.poweronCron);
    },
  },

  methods: {
    update(patch) {
      this.$emit('update', patch);
    },
  },
};
</script>

<template>
  <div
    class="vm-row"
    :class="{ enabled: value.enabled }"
  >
    <label class="head">
      <input
        type="checkbox"
        :checked="value.enabled"
        @change="update({ enabled: $event.target.checked })"
      >
      <span class="title">{{ value.vm }}</span>
      <span class="text-muted">{{ value.status }}</span>
      <span
        v-if="value.missing"
        class="text-warning"
      >VM no longer exists</span>
    </label>
    <div
      v-if="value.enabled"
      class="grid"
    >
      <div>
        <label class="label">Shutdown (cron)</label>
        <input
          :value="value.shutdownCron"
          type="text"
          spellcheck="false"
          placeholder="0 21 * * *  (empty = none)"
          class="field"
          :class="{ invalid: shutdownError }"
          @input="update({ shutdownCron: $event.target.value })"
        >
        <p
          v-if="shutdownError"
          class="text-error hint"
        >
          {{ shutdownError }}
        </p>
      </div>
      <div>
        <label class="label">Power-on (cron)</label>
        <input
          :value="value.poweronCron"
          type="text"
          spellcheck="false"
          placeholder="0 7 * * *  (empty = none)"
          class="field"
          :class="{ invalid: poweronError }"
          @input="update({ poweronCron: $event.target.value })"
        >
        <p
          v-if="poweronError"
          class="text-error hint"
        >
          {{ poweronError }}
        </p>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.vm-row {
  border: 1px solid var(--border, #ccc);
  border-radius: var(--border-radius, 4px);
  padding: 6px 12px;
  margin-top: 6px;

  &.enabled { border-color: var(--primary, #0055a4); }
  .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; cursor: pointer; }
  .title { font-weight: 600; overflow-wrap: anywhere; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px 14px; margin-top: 8px; }
  .label { display: block; font-weight: 600; margin-bottom: 4px; }
  .field {
    width: 100%;
    padding: 6px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
    &.invalid { border-color: var(--error, #d32f2f); }
  }
  .hint { margin: 4px 0 0; font-size: 0.9em; }
  input:focus-visible { outline: 2px solid var(--primary, #0055a4); outline-offset: 1px; }
}
</style>
