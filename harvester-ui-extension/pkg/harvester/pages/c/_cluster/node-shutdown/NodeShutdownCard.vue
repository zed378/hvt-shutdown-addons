<script>
const STRATEGIES = ['migrate', 'stop', 'force'];

// Plain-language presets so operators never have to write cron by hand.
const CRON_PRESETS = [
  { label: 'Every day at 01:00', value: '0 1 * * *' },
  { label: 'Every day at 02:00', value: '0 2 * * *' },
  { label: 'Every Sunday at 02:00', value: '0 2 * * 0' },
];

export default {
  name: 'NodeShutdownCard',

  props: {
    value: {
      type:     Object,
      required: true,
    },
    vmOptions: {
      type:    Array,
      default: () => [],
    },
  },

  computed: {
    selectedCount() {
      return (this.value.vms || []).length;
    },
    poweronSelectedCount() {
      return (this.value.poweronVms || []).length;
    },
    statusText() {
      const parts = [];

      if (this.value.nodeEnabled) {
        parts.push('shutdown node on');
      }
      if (this.value.vmEnabled) {
        parts.push(`shutdown VMs (${ this.selectedCount })`);
      }
      if (this.value.poweronNodeEnabled) {
        parts.push(`poweron IPMI on`);
      }
      if (this.value.poweronVmEnabled) {
        parts.push(`poweron VMs (${ this.poweronSelectedCount })`);
      }
      return parts.length ? parts.join(' | ') : 'schedules off';
    },
    startOpen() {
      return !!(this.value.nodeEnabled || this.value.vmEnabled || this.value.poweronNodeEnabled || this.value.poweronVmEnabled);
    },
  },

  methods: {
    update(patch) {
      this.$emit('input', { ...this.value, ...patch });
    },
    togglePoweronVm(key) {
      const vms = [...(this.value.poweronVms || [])];
      const i = vms.indexOf(key);

      if (i >= 0) {
        vms.splice(i, 1);
      } else {
        vms.push(key);
      }
      this.update({ poweronVms: vms });
    },
    selectAllPoweron() {
      this.update({ poweronVms: [...this.vmOptions] });
    },
    clearAllPoweron() {
      this.update({ poweronVms: [] });
    },
    cronPresets() {
      return CRON_PRESETS;
    },
    cronPreset(cron) {
      const hit = CRON_PRESETS.find((p) => p.value === (cron || '').trim());

      return hit ? hit.value : 'custom';
    },
    presetSummary(cron) {
      const hit = CRON_PRESETS.find((p) => p.value === (cron || '').trim());

      return hit ? `Runs ${ hit.label.charAt(0).toLowerCase() + hit.label.slice(1) } (UTC).` : `Runs on a custom schedule: ${ cron || '(empty)' } (UTC).`;
    },
    onPreset(field, chosen) {
      if (chosen !== 'custom') {
        this.update({ [field]: chosen });
      }
      // 'custom' keeps the current value and reveals the text field below.
    },
    toggleVm(key) {
      const vms = [...(this.value.vms || [])];
      const i = vms.indexOf(key);

      if (i >= 0) {
        vms.splice(i, 1);
      } else {
        vms.push(key);
      }
      this.update({ vms });
    },
    selectAll() {
      this.update({ vms: [...this.vmOptions] });
    },
    clearAll() {
      this.update({ vms: [] });
    },
  },
};
</script>

<template>
  <div class="node-card">
    <details :open="startOpen">
      <summary class="node-card-summary">
        <span class="node-card-title">{{ value.node }}</span>
        <span class="text-muted">{{ statusText }}</span>
      </summary>

    <label class="checkbox">
      <input
        type="checkbox"
        :checked="value.nodeEnabled"
        @change="update({ nodeEnabled: $event.target.checked })"
      />
      Shut down this node on a schedule
    </label>

    <template v-if="value.nodeEnabled">
      <label class="label mt-10">Node shutdown time</label>
      <select
        :value="cronPreset(value.nodeCron)"
        class="field"
        @change="onPreset('nodeCron', $event.target.value)"
      >
        <option
          v-for="p in cronPresets()"
          :key="p.value"
          :value="p.value"
        >{{ p.label }}</option>
        <option value="custom">Custom schedule...</option>
      </select>
      <input
        v-if="cronPreset(value.nodeCron) === 'custom'"
        :value="value.nodeCron"
        type="text"
        spellcheck="false"
        placeholder="0 2 * * *"
        class="field mt-10"
        @input="update({ nodeCron: $event.target.value })"
      />
      <p class="text-muted mt-5">{{ presetSummary(value.nodeCron) }}</p>
    </template>

    <h4 class="mt-20">Virtual machines</h4>
    <label class="checkbox">
      <input
        type="checkbox"
        :checked="value.vmEnabled"
        @change="update({ vmEnabled: $event.target.checked })"
      />
      Shut down selected VMs on a schedule
    </label>

    <template v-if="value.vmEnabled">
      <label class="label mt-10">VM shutdown time (one time for all selected VMs)</label>
      <select
        :value="cronPreset(value.vmCron)"
        class="field"
        @change="onPreset('vmCron', $event.target.value)"
      >
        <option
          v-for="p in cronPresets()"
          :key="p.value"
          :value="p.value"
        >{{ p.label }}</option>
        <option value="custom">Custom schedule...</option>
      </select>
      <input
        v-if="cronPreset(value.vmCron) === 'custom'"
        :value="value.vmCron"
        type="text"
        spellcheck="false"
        placeholder="0 1 * * *"
        class="field mt-10"
        @input="update({ vmCron: $event.target.value })"
      />
      <p class="text-muted mt-5">{{ presetSummary(value.vmCron) }}</p>

      <label class="label mt-10">VM strategy</label>
      <select
        :value="value.vmStrategy"
        class="field"
        @change="update({ vmStrategy: $event.target.value })"
      >
        <option
          v-for="s in ['migrate', 'stop', 'force']"
          :key="s"
          :value="s"
        >{{ s }}</option>
      </select>
      <p class="text-muted mt-5">
        <b>migrate</b>: live-migrate VMs to surviving nodes (else stop).
        <b>stop</b>: gracefully stop VMs. <b>force</b>: kill immediately.
      </p>

      <div class="row mt-10">
        <button
          type="button"
          class="btn role-secondary"
          :disabled="!vmOptions.length"
          @click="selectAll"
        >Select all</button>
        <button
          type="button"
          class="btn role-secondary"
          :disabled="!selectedCount"
          @click="clearAll"
        >Clear</button>
        <span class="text-muted">{{ selectedCount }} of {{ vmOptions.length }} selected.</span>
      </div>
      <div
        v-if="vmOptions.length"
        class="nodes mt-10"
      >
        <label
          v-for="vm in vmOptions"
          :key="vm"
          class="checkbox"
        >
          <input
            type="checkbox"
            :checked="(value.vms || []).includes(vm)"
            @change="toggleVm(vm)"
          />
          {{ vm }}
        </label>
      </div>
      <p
        v-else
        class="text-muted mt-10"
      >No VMs found on this node, or the list could not be loaded. Saving with nothing checked targets all VMs on this node.</p>
      <p class="text-muted mt-10">Stopped VMs stay off after the node powers back on unless scheduled to start.</p>
    </template>

    <hr class="mt-20 mb-20" />
    <h4 class="mt-10">⚡ Power-on (IPMI &amp; VMs)</h4>
    <p class="text-muted mb-10">Configure wake-up schedule via IPMI over LAN and VM automatic startup.</p>

    <label class="checkbox">
      <input
        type="checkbox"
        :checked="value.poweronNodeEnabled"
        @change="update({ poweronNodeEnabled: $event.target.checked })"
      />
      Power on this node on a schedule (via IPMI)
    </label>

    <template v-if="value.poweronNodeEnabled">
      <label class="label mt-10">BMC / IPMI IP Address</label>
      <input
        :value="value.bmcIp"
        type="text"
        spellcheck="false"
        placeholder="e.g. 192.168.10.51"
        class="field"
        @input="update({ bmcIp: $event.target.value })"
      />
      <p class="text-muted mt-5">Out-of-band management IP for this physical server.</p>

      <label class="label mt-10">Node power-on time</label>
      <select
        :value="cronPreset(value.poweronNodeCron)"
        class="field"
        @change="onPreset('poweronNodeCron', $event.target.value)"
      >
        <option
          v-for="p in cronPresets()"
          :key="p.value"
          :value="p.value"
        >{{ p.label }}</option>
        <option value="custom">Custom schedule...</option>
      </select>
      <input
        v-if="cronPreset(value.poweronNodeCron) === 'custom'"
        :value="value.poweronNodeCron"
        type="text"
        spellcheck="false"
        placeholder="0 6 * * 1-5"
        class="field mt-10"
        @input="update({ poweronNodeCron: $event.target.value })"
      />
      <p class="text-muted mt-5">{{ presetSummary(value.poweronNodeCron) }}</p>
    </template>

    <h4 class="mt-15">Power-on virtual machines</h4>
    <label class="checkbox">
      <input
        type="checkbox"
        :checked="value.poweronVmEnabled"
        @change="update({ poweronVmEnabled: $event.target.checked })"
      />
      Start selected VMs on a schedule
    </label>

    <template v-if="value.poweronVmEnabled">
      <label class="label mt-10">VM power-on time (starts automatically after node is Ready)</label>
      <select
        :value="cronPreset(value.poweronVmCron)"
        class="field"
        @change="onPreset('poweronVmCron', $event.target.value)"
      >
        <option
          v-for="p in cronPresets()"
          :key="p.value"
          :value="p.value"
        >{{ p.label }}</option>
        <option value="custom">Custom schedule...</option>
      </select>
      <input
        v-if="cronPreset(value.poweronVmCron) === 'custom'"
        :value="value.poweronVmCron"
        type="text"
        spellcheck="false"
        placeholder="0 6 * * 1-5"
        class="field mt-10"
        @input="update({ poweronVmCron: $event.target.value })"
      />
      <p class="text-muted mt-5">{{ presetSummary(value.poweronVmCron) }}</p>

      <div class="row mt-10">
        <button
          type="button"
          class="btn role-secondary"
          :disabled="!vmOptions.length"
          @click="selectAllPoweron"
        >Select all</button>
        <button
          type="button"
          class="btn role-secondary"
          :disabled="!poweronSelectedCount"
          @click="clearAllPoweron"
        >Clear</button>
        <span class="text-muted">{{ poweronSelectedCount }} of {{ vmOptions.length }} selected.</span>
      </div>
      <div
        v-if="vmOptions.length"
        class="nodes mt-10"
      >
        <label
          v-for="vm in vmOptions"
          :key="vm"
          class="checkbox"
        >
          <input
            type="checkbox"
            :checked="(value.poweronVms || []).includes(vm)"
            @change="togglePoweronVm(vm)"
          />
          {{ vm }}
        </label>
      </div>
      <p
        v-else
        class="text-muted mt-10"
      >No VMs found on this node. Saving with nothing checked targets all VMs on this node.</p>
    </template>
    </details>
  </div>
</template>

<style lang="scss" scoped>
.node-card {
  border: 1px solid var(--border, #ccc);
  border-radius: var(--border-radius, 4px);
  padding: 14px;
  margin-top: 12px;

  .node-card-title { margin: 0; font-size: 1.1em; font-weight: 600; }
  .node-card-summary { display: flex; gap: 10px; align-items: baseline; cursor: pointer; padding: 8px 2px; }
  h4 { margin-bottom: 4px; }
  .label { display: block; font-weight: 600; margin-bottom: 6px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .field {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
  }
  .checkbox { display: block; margin: 2px 0; padding: 6px 0; cursor: pointer; }
  .nodes { max-height: 220px; overflow: auto; border: 1px solid var(--border, #ccc); border-radius: 4px; padding: 8px; }
  .nodes .checkbox { overflow-wrap: anywhere; }
  .field:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {
    outline: 2px solid var(--primary, #0055a4);
    outline-offset: 1px;
  }
  .mb-5 { margin-bottom: 5px; } .mb-10 { margin-bottom: 10px; }
  .mt-5 { margin-top: 5px; } .mt-10 { margin-top: 10px; } .mt-20 { margin-top: 20px; }
}
</style>
