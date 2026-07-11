<script>
import jsyaml from 'js-yaml';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/node-shutdown';
const STRATEGIES = ['migrate', 'stop', 'force'];

export default {
  name: 'HarvesterNodeShutdown',

  data() {
    return {
      addon:       null,
      token:       '',
      schedule:    { enabled: false, cron: '0 2 * * *', nodes: [], vmStrategy: 'migrate' },
      nodeOptions: [],
      strategies:  STRATEGIES,
      saving:      false,
      saved:       false,
      saveError:   '',
      loadError:   '',
    };
  },

  async fetch() {
    try {
      this.addon = await this.$store.dispatch('harvester/find', { type: ADDON_TYPE, id: ADDON_ID });
      const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

      this.token = parsed?.auth?.token || '';
      const s = parsed?.schedule || {};

      this.schedule = {
        enabled:    !!s.enabled,
        cron:       s.cron || '0 2 * * *',
        nodes:      Array.isArray(s.nodes) ? s.nodes : [],
        vmStrategy: STRATEGIES.includes(s.vmStrategy) ? s.vmStrategy : 'migrate',
      };
    } catch (e) {
      this.loadError = e?.message || String(e);
    }
    // Cluster nodes for the picker (best-effort).
    try {
      const nodes = await this.$store.dispatch('harvester/findAll', { type: 'node' });

      this.nodeOptions = (nodes || []).map((n) => n.metadata?.name).filter(Boolean);
    } catch (e) {
      this.nodeOptions = [];
    }
  },

  computed: {
    weak() {
      return this.token && this.token.length < 32;
    },
  },

  methods: {
    generate() {
      const a = new Uint8Array(32);

      (window.crypto || window.msCrypto).getRandomValues(a);
      this.token = Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
    },

    toggleNode(name) {
      const i = this.schedule.nodes.indexOf(name);

      if (i >= 0) {
        this.schedule.nodes.splice(i, 1);
      } else {
        this.schedule.nodes.push(name);
      }
    },

    async save() {
      this.saving = true;
      this.saved = false;
      this.saveError = '';
      try {
        const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

        if (!parsed.auth) {
          parsed.auth = {};
        }
        parsed.auth.token = this.token;
        parsed.schedule = {
          enabled:    this.schedule.enabled,
          cron:       this.schedule.cron,
          nodes:      this.schedule.nodes,
          vmStrategy: this.schedule.vmStrategy,
        };
        this.addon.spec.valuesContent = jsyaml.dump(parsed);
        await this.addon.save();
        this.saved = true;
      } catch (e) {
        this.saveError = e?.message || String(e);
      } finally {
        this.saving = false;
      }
    },
  },
};
</script>

<template>
  <div class="node-shutdown">
    <h1 class="mb-10">
      Node Shutdown
    </h1>

    <div v-if="loadError" class="banner-error mb-20">
      Could not load the <code>node-shutdown</code> add-on: <strong>{{ loadError }}</strong>
    </div>

    <template v-else>
      <!-- Authentication token -->
      <h3>Authentication token</h3>
      <p class="text-muted mb-10">
        Used to authorize shutdown requests. Applies within ~1 minute.
      </p>
      <div class="row">
        <input
          v-model="token"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="Enter or generate a strong token"
          class="field"
        />
        <button type="button" class="btn role-secondary" @click="generate">Generate</button>
      </div>
      <p v-if="weak" class="text-warning mt-5">
        Short token — use at least 32 characters (e.g. <code>openssl rand -hex 32</code>).
      </p>

      <!-- Scheduled / selected-node shutdown -->
      <h3 class="mt-30">Scheduled shutdown</h3>
      <label class="checkbox">
        <input v-model="schedule.enabled" type="checkbox" />
        Enable a scheduled shutdown
      </label>

      <template v-if="schedule.enabled">
        <label class="label mt-15">Cron schedule</label>
        <input
          v-model="schedule.cron"
          type="text"
          spellcheck="false"
          placeholder="0 2 * * *"
          class="field"
        />
        <p class="text-muted mt-5">Standard cron (UTC). Example: <code>0 2 * * *</code> = 02:00 daily.</p>

        <label class="label mt-15">VM strategy</label>
        <select v-model="schedule.vmStrategy" class="field">
          <option v-for="s in strategies" :key="s" :value="s">{{ s }}</option>
        </select>
        <p class="text-muted mt-5">
          <b>migrate</b>: live-migrate VMs to surviving nodes (else stop) — for selected-node shutdown.
          <b>stop</b>: gracefully stop VMs. <b>force</b>: kill immediately.
        </p>

        <label class="label mt-15">Target nodes</label>
        <p class="text-muted mt-5 mb-5">Leave all unchecked to shut down the whole cluster.</p>
        <div v-if="nodeOptions.length" class="nodes">
          <label v-for="n in nodeOptions" :key="n" class="checkbox">
            <input type="checkbox" :checked="schedule.nodes.includes(n)" @change="toggleNode(n)" />
            {{ n }}
          </label>
        </div>
        <p v-else class="text-muted">(Could not list nodes — the whole cluster will be targeted.)</p>
      </template>

      <button type="button" class="btn role-primary mt-20" :disabled="saving" @click="save">
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
      <p v-if="saved" class="text-success mt-10">Saved.</p>
      <p v-if="saveError" class="text-error mt-10">{{ saveError }}</p>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.node-shutdown {
  padding: 20px;
  max-width: 680px;

  h3 { margin-bottom: 4px; }
  .label { display: block; font-weight: 600; margin-bottom: 6px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .field {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
  }
  .checkbox { display: block; margin: 4px 0; cursor: pointer; }
  .nodes { max-height: 220px; overflow: auto; border: 1px solid var(--border, #ccc); border-radius: 4px; padding: 8px; }
  .mb-5 { margin-bottom: 5px; } .mb-10 { margin-bottom: 10px; } .mb-20 { margin-bottom: 20px; }
  .mt-5 { margin-top: 5px; } .mt-10 { margin-top: 10px; } .mt-15 { margin-top: 15px; }
  .mt-20 { margin-top: 20px; } .mt-30 { margin-top: 30px; }
  .banner-error {
    padding: 10px 12px; border-radius: 6px;
    background: rgba(200, 0, 0, 0.1); border: 1px solid rgba(200, 0, 0, 0.3);
  }
}
</style>
