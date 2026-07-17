<script>
import jsyaml from 'js-yaml';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/vpn';

/**
 * Config form for ONE provider inside the single `vpn` add-on.
 *
 * All four providers (NetBird, Tailscale, ZeroTier, OpenVPN) live in one add-on,
 * so every page edits its own section of the SAME valuesContent document. This
 * component reads/writes only `vpn.<provider>` and leaves the other providers'
 * sections untouched — otherwise saving the Tailscale page would wipe NetBird's
 * config.
 */
export default {
  name: 'VpnProviderForm',

  props: {
    /** Key under `vpn:` in valuesContent, e.g. 'netbird'. */
    provider: {
      type:     String,
      required: true
    },
    title: {
      type:     String,
      required: true
    },
    description: {
      type:    String,
      default: ''
    },
    /**
     * [{ key, label, type: 'text'|'password'|'textarea'|'checkbox',
     *    placeholder, hint, required }]
     */
    fields: {
      type:     Array,
      required: true
    },
  },

  data() {
    return {
      addon:     null,
      enabled:   false,
      values:    {},
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
      const cfg = parsed?.vpn?.[this.provider] || {};
      const values = {};

      this.enabled = cfg.enabled === true;
      this.fields.forEach((f) => {
        const current = cfg[f.key];

        values[f.key] = f.type === 'checkbox' ? current === true : (current ?? '');
      });
      this.values = values;
    } catch (e) {
      this.loadError = e?.message || String(e);
    }
  },

  computed: {
    // The provider can't deploy while the add-on itself is off, however valid
    // its config is — say so rather than letting the user wonder.
    addonEnabled() {
      return this.addon?.spec?.enabled === true;
    },

    missing() {
      return this.fields
        .filter((f) => f.required && !String(this.values[f.key] ?? '').trim())
        .map((f) => f.label);
    },

    canSave() {
      if (this.saving || !this.addon || this.loadError) {
        return false;
      }

      // Turning a provider OFF must always be possible, even with invalid config.
      return !this.enabled || this.missing.length === 0;
    },
  },

  methods: {
    async save() {
      this.saving = true;
      this.saved = false;
      this.saveError = '';

      try {
        // Re-parse rather than reuse a cached object: another provider's page may
        // have written to valuesContent since this page loaded.
        const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};

        if (!parsed.vpn) {
          parsed.vpn = {};
        }
        if (!parsed.vpn[this.provider]) {
          parsed.vpn[this.provider] = {};
        }

        const target = parsed.vpn[this.provider];

        target.enabled = this.enabled;
        this.fields.forEach((f) => {
          const val = this.values[f.key];

          target[f.key] = f.type === 'checkbox' ? val === true : String(val ?? '').trim();
        });

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
  <div class="vpn-provider">
    <h1 class="mb-10">
      {{ title }}
    </h1>
    <p v-if="description" class="text-muted mb-20">
      {{ description }}
    </p>

    <div v-if="loadError" class="banner-error mb-20">
      Could not load the <code>vpn</code> add-on: <strong>{{ loadError }}</strong>
      <div class="mt-10">
        Make sure the <code>vpn</code> add-on is installed in <code>harvester-system</code>.
      </div>
    </div>

    <template v-else>
      <div v-if="addon && !addonEnabled" class="banner-warning mb-20">
        The <code>vpn</code> add-on is currently <strong>disabled</strong>, so nothing
        will deploy. You can still save this config — enable the add-on under
        <em>Advanced &rarr; Add-ons</em> to roll it out.
      </div>

      <label class="checkbox mb-20">
        <input v-model="enabled" type="checkbox" />
        <span>Enable {{ title }} on all nodes</span>
      </label>

      <div v-for="f in fields" :key="f.key" class="field-row">
        <template v-if="f.type === 'checkbox'">
          <label class="checkbox">
            <input v-model="values[f.key]" type="checkbox" />
            <span>{{ f.label }}</span>
          </label>
        </template>

        <template v-else>
          <label class="label">
            {{ f.label }}
            <span v-if="f.required" class="req">*</span>
          </label>
          <textarea
            v-if="f.type === 'textarea'"
            v-model="values[f.key]"
            :placeholder="f.placeholder"
            spellcheck="false"
            rows="10"
            class="field mono"
          />
          <input
            v-else
            v-model="values[f.key]"
            :type="f.type === 'password' ? 'password' : 'text'"
            :placeholder="f.placeholder"
            autocomplete="off"
            spellcheck="false"
            class="field"
          />
        </template>
        <p v-if="f.hint" class="hint">
          {{ f.hint }}
        </p>
      </div>

      <button type="button" class="btn role-primary mt-20" :disabled="!canSave" @click="save">
        {{ saving ? 'Saving…' : 'Save' }}
      </button>

      <p v-if="enabled && missing.length" class="text-muted mt-10">
        Required: {{ missing.join(', ') }}
      </p>
      <p v-if="saved" class="text-success mt-10">
        Saved — {{ enabled ? 'the agent will roll out on all nodes shortly.' : 'the agent has been removed.' }}
      </p>
      <p v-if="saveError" class="text-error mt-10">
        {{ saveError }}
      </p>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.vpn-provider {
  padding: 20px;
  max-width: 640px;

  .label { display: block; font-weight: 600; margin-bottom: 6px; }
  .req { color: var(--error, #c00); }
  .field-row { margin-bottom: 20px; }
  .field {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
  }
  .mono { font-family: monospace; font-size: 12px; }
  .hint { font-size: 12px; color: var(--muted, #888); margin-top: 4px; }
  .checkbox {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
  }
  .mb-10 { margin-bottom: 10px; }
  .mb-20 { margin-bottom: 20px; }
  .mt-10 { margin-top: 10px; }
  .mt-20 { margin-top: 20px; }
  .banner-error, .banner-warning {
    padding: 10px 12px;
    border-radius: 6px;
  }
  .banner-error {
    background: rgba(200, 0, 0, 0.1);
    border: 1px solid rgba(200, 0, 0, 0.3);
  }
  .banner-warning {
    background: rgba(220, 150, 0, 0.1);
    border: 1px solid rgba(220, 150, 0, 0.3);
  }
}
</style>
