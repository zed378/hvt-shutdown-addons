<template>
  <div class="node-shutdown">
    <h1>Node Shutdown</h1>
    <p class="text-muted mb-20">
      Set the authentication token used to trigger cluster shutdown. This updates the
      <code>node-shutdown</code> add-on; the change applies within ~1 minute.
    </p>

    <div v-if="loadError" class="banner-error">
      Could not load the <code>node-shutdown</code> add-on:
      <strong>{{ loadError }}</strong>
    </div>

    <div v-else>
      <label class="label">Authentication Token</label>
      <div class="token-row">
        <input
          v-model="token"
          type="password"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          placeholder="Enter or generate a strong token"
          class="token-input"
        />
        <button type="button" class="btn role-secondary" @click="generate">Generate</button>
      </div>
      <p v-if="token && token.length < 32" class="text-warning mt-5">
        This token is short. Use at least 32 characters (e.g. <code>openssl rand -hex 32</code>).
      </p>

      <button type="button" class="btn role-primary mt-20" :disabled="saving || !token" @click="save">
        {{ saving ? 'Saving…' : 'Save token' }}
      </button>

      <p v-if="saved" class="text-success mt-10">Token saved — it applies on all nodes within ~1 minute.</p>
      <p v-if="saveError" class="text-error mt-10">{{ saveError }}</p>
    </div>
  </div>
</template>

<script>
import jsyaml from 'js-yaml';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/node-shutdown';

export default {
  name: 'NodeShutdownPage',

  data() {
    return {
      addon:     null,
      token:     '',
      saving:    false,
      saved:     false,
      saveError: '',
      loadError: '',
    };
  },

  async fetch() {
    // Harvester resources live in the `harvester` store (see the dashboard's
    // "[harvester] harvesterhci.io.addon" lookups). Fall back to `cluster` store.
    for (const s of ['harvester', 'cluster']) {
      try {
        this.addon = await this.$store.dispatch(`${ s }/find`, { type: ADDON_TYPE, id: ADDON_ID });
        if (this.addon) {
          break;
        }
      } catch (e) {
        this.loadError = e?.message || String(e);
      }
    }
    if (this.addon) {
      this.loadError = '';
      try {
        const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

        this.token = parsed?.auth?.token || '';
      } catch (e) {
        this.loadError = `Could not parse add-on values: ${ e?.message || e }`;
      }
    } else if (!this.loadError) {
      this.loadError = 'add-on not found';
    }
  },

  methods: {
    generate() {
      const a = new Uint8Array(32);

      (window.crypto || window.msCrypto).getRandomValues(a);
      this.token = Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
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

<style scoped>
.node-shutdown { padding: 20px; max-width: 640px; }
.label { display: block; font-weight: 600; margin-bottom: 6px; }
.token-row { display: flex; gap: 8px; align-items: center; }
.token-input { flex: 1; padding: 8px 10px; }
.mb-20 { margin-bottom: 20px; }
.mt-5 { margin-top: 5px; }
.mt-10 { margin-top: 10px; }
.mt-20 { margin-top: 20px; }
.banner-error {
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(200, 0, 0, 0.1);
  border: 1px solid rgba(200, 0, 0, 0.3);
}
</style>
