<template>
  <div class="mt-20">
    <h3>Node Shutdown Configuration</h3>
    <div class="row mb-20 mt-10">
      <div class="col span-6">
        <label>Authentication Token <span class="text-error">*</span></label>
        <input
          v-model="token"
          type="password"
          placeholder="Enter secure token (openssl rand -hex 32)"
          autocomplete="off"
          autocorrect="off"
          autocapitalize="off"
          spellcheck="false"
          @input="updateValues"
          class="form-control"
        />
        <p v-if="token && token.length < 32" class="text-warning mt-5">
          This token is short. Use a strong random value, e.g.
          <code>openssl rand -hex 32</code>.
        </p>
        <p class="text-muted mt-5">
          This token is required for the node-shutdown webhook to accept
          requests. It is stored in the add-on's values automatically. Anyone who
          can read this add-on can read the token, so treat add-on access as
          sensitive and rotate the token periodically.
        </p>
      </div>
    </div>
  </div>
</template>

<script>
import jsyaml from "js-yaml";

export default {
  props: {
    value: {
      type: Object,
      required: true,
    },
  },
  data() {
    return {
      token: "",
    };
  },
  mounted() {
    this.parseValues();
  },
  methods: {
    parseValues() {
      try {
        const parsed = jsyaml.load(this.value.spec?.valuesContent || "");
        if (parsed && parsed.auth && parsed.auth.token) {
          this.token = parsed.auth.token;
        }
      } catch (e) {
        console.error("Error parsing valuesContent", e);
      }
    },
    updateValues() {
      try {
        const parsed = jsyaml.load(this.value.spec?.valuesContent || "") || {};
        if (!parsed.auth) {
          parsed.auth = {};
        }
        parsed.auth.token = this.token;

        // Use Vue's $set to ensure reactivity in the parent's value object
        if (!this.value.spec) {
          this.$set(this.value, "spec", {});
        }
        this.$set(this.value.spec, "valuesContent", jsyaml.dump(parsed));
      } catch (e) {
        console.error("Error updating valuesContent", e);
      }
    },
  },
};
</script>
