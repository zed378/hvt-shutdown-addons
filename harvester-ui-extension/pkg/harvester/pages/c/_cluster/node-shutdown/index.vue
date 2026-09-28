<script>
import jsyaml from 'js-yaml';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/node-shutdown';

// Auth key only. All shutdown / power-on scheduling lives on Power Schedules.
export default {
  name: 'HarvesterNodeShutdownAuthKey',

  data() {
    return {
      addon:     null,
      token:     '',
      reveal:    false,
      saving:    false,
      saved:     false,
      saveError: '',
      loadError: '',
    };
  },

  async fetch() {
    try {
      this.addon = await this.$store.dispatch('harvester/find', { type: ADDON_TYPE, id: ADDON_ID });
      const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

      this.token = parsed?.auth?.token || '';
    } catch (e) {
      this.loadError = e?.message || String(e);
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
      this.reveal = true;
    },

    async save() {
      this.saving = true;
      this.saved = false;
      this.saveError = '';
      try {
        const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

        parsed.auth = { ...(parsed.auth || {}), token: this.token };
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
      Node Shutdown Auth Key
    </h1>

    <div
      v-if="loadError"
      class="banner-error mb-20"
    >
      Could not load the <code>node-shutdown</code> add-on: <strong>{{ loadError }}</strong>
    </div>

    <template v-else>
      <p class="text-muted mb-10">
        Bearer token that authorizes shutdown, power-on and BMC test requests to the node-shutdown API
        (UPS scripts, schedule CronJobs and this dashboard). Changes apply within about a minute.
      </p>
      <div class="row">
        <input
          v-model="token"
          :type="reveal ? 'text' : 'password'"
          autocomplete="off"
          spellcheck="false"
          placeholder="Enter or generate a strong token"
          class="field"
        >
        <button
          type="button"
          class="btn role-secondary"
          @click="reveal = !reveal"
        >
          {{ reveal ? 'Hide' : 'Show' }}
        </button>
        <button
          type="button"
          class="btn role-secondary"
          @click="generate"
        >
          Generate
        </button>
      </div>
      <p
        v-if="weak"
        class="text-warning mt-5"
      >
        Short token — use at least 32 characters (e.g. <code>openssl rand -hex 32</code>).
      </p>
      <p class="text-muted mt-10">
        After changing it, update anything outside the cluster that calls the API (e.g. UPS shutdown scripts).
        Shutdown and power-on schedules are configured on the <b>Power Schedules</b> page.
      </p>

      <button
        type="button"
        class="btn role-primary mt-20"
        :disabled="saving || !token"
        @click="save"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
      <p
        v-if="saved"
        class="text-success mt-10"
      >
        Saved.
      </p>
      <p
        v-if="saveError"
        class="text-error mt-10"
      >
        {{ saveError }}
      </p>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.node-shutdown {
  padding: 20px;
  max-width: 680px;

  .row { display: flex; gap: 8px; align-items: center; }
  .field {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
  }
  .mb-10 { margin-bottom: 10px; } .mb-20 { margin-bottom: 20px; }
  .mt-5 { margin-top: 5px; } .mt-10 { margin-top: 10px; } .mt-20 { margin-top: 20px; }
  .banner-error {
    padding: 10px 12px; border-radius: 6px;
    background: rgba(200, 0, 0, 0.1); border: 1px solid rgba(200, 0, 0, 0.3);
  }
}
</style>
