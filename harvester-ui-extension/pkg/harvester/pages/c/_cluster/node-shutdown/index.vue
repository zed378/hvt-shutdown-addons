<script>
import jsyaml from 'js-yaml';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/node-shutdown';

export default {
  name: 'HarvesterNodeShutdown',

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

<template>
  <div class="node-shutdown">
    <h1 class="mb-10">
      Node Shutdown
    </h1>
    <p class="text-muted mb-20">
      Set the authentication token used to trigger cluster shutdown. This updates the
      <code>node-shutdown</code> add-on; the change applies on all nodes within ~1 minute.
    </p>

    <div v-if="loadError" class="banner-error mb-20">
      Could not load the <code>node-shutdown</code> add-on: <strong>{{ loadError }}</strong>
    </div>

    <template v-else>
      <label class="label">Authentication Token</label>
      <div class="token-row">
        <input
          v-model="token"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="Enter or generate a strong token"
          class="token-input"
        />
        <button type="button" class="btn role-secondary" @click="generate">
          Generate
        </button>
      </div>
      <p v-if="weak" class="text-warning mt-5">
        This token is short. Use at least 32 characters (e.g. <code>openssl rand -hex 32</code>).
      </p>

      <button
        type="button"
        class="btn role-primary mt-20"
        :disabled="saving || !token"
        @click="save"
      >
        {{ saving ? 'Saving…' : 'Save token' }}
      </button>

      <p v-if="saved" class="text-success mt-10">
        Token saved — it applies on all nodes within ~1 minute.
      </p>
      <p v-if="saveError" class="text-error mt-10">
        {{ saveError }}
      </p>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.node-shutdown {
  padding: 20px;
  max-width: 640px;

  .label { display: block; font-weight: 600; margin-bottom: 6px; }
  .token-row { display: flex; gap: 8px; align-items: center; }
  .token-input {
    flex: 1;
    padding: 8px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
  }
  .banner-error {
    padding: 10px 12px;
    border-radius: 6px;
    background: rgba(200, 0, 0, 0.1);
    border: 1px solid rgba(200, 0, 0, 0.3);
  }
}
</style>
