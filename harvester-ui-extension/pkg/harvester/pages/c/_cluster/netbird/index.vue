<script>
import jsyaml from 'js-yaml';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/netbird';

export default {
  name: 'HarvesterNetbird',

  data() {
    return {
      addon:         null,
      managementUrl: '',
      setupKey:      '',
      saving:        false,
      saved:         false,
      saveError:     '',
      loadError:     '',
    };
  },

  async fetch() {
    try {
      this.addon = await this.$store.dispatch('harvester/find', { type: ADDON_TYPE, id: ADDON_ID });
      const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};
      const nb = parsed?.netbird || {};

      this.managementUrl = nb.managementUrl || '';
      this.setupKey = nb.setupKey || '';
    } catch (e) {
      this.loadError = e?.message || String(e);
    }
  },

  computed: {
    canSave() {
      return !this.saving && this.managementUrl.trim() && this.setupKey.trim();
    },
  },

  methods: {
    async save() {
      this.saving = true;
      this.saved = false;
      this.saveError = '';
      try {
        const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

        if (!parsed.netbird) {
          parsed.netbird = {};
        }
        parsed.netbird.managementUrl = this.managementUrl.trim();
        parsed.netbird.setupKey = this.setupKey.trim();
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
  <div class="netbird">
    <h1 class="mb-10">
      NetBird
    </h1>
    <p class="text-muted mb-20">
      Enroll every Harvester node into your NetBird network. Enter your management
      domain and a (reusable) setup key; the agent DaemonSet deploys once both are set.
    </p>

    <div v-if="loadError" class="banner-error mb-20">
      Could not load the <code>netbird</code> add-on: <strong>{{ loadError }}</strong>
    </div>

    <template v-else>
      <label class="label">Management URL (domain)</label>
      <input
        v-model="managementUrl"
        type="text"
        autocomplete="off"
        spellcheck="false"
        placeholder="https://api.netbird.io  (or your self-hosted URL)"
        class="field"
      />

      <label class="label mt-20">Setup key</label>
      <input
        v-model="setupKey"
        type="password"
        autocomplete="off"
        spellcheck="false"
        placeholder="Reusable setup key"
        class="field"
      />

      <button type="button" class="btn role-primary mt-20" :disabled="!canSave" @click="save">
        {{ saving ? 'Saving…' : 'Save & enroll' }}
      </button>

      <p v-if="saved" class="text-success mt-10">
        Saved — the NetBird agent will roll out on all nodes shortly.
      </p>
      <p v-if="saveError" class="text-error mt-10">
        {{ saveError }}
      </p>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.netbird {
  padding: 20px;
  max-width: 640px;

  .label { display: block; font-weight: 600; margin-bottom: 6px; }
  .field {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
  }
  .mb-10 { margin-bottom: 10px; }
  .mb-20 { margin-bottom: 20px; }
  .mt-10 { margin-top: 10px; }
  .mt-20 { margin-top: 20px; }
  .banner-error {
    padding: 10px 12px;
    border-radius: 6px;
    background: rgba(200, 0, 0, 0.1);
    border: 1px solid rgba(200, 0, 0, 0.3);
  }
}
</style>
