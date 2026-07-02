<template>
  <div class="mt-20">
    <h3>Node Shutdown Configuration</h3>
    <div class="row mb-20 mt-10">
      <div class="col span-6">
        <label>Authentication Token <span class="text-error">*</span></label>
        <input
          v-model="token"
          type="password"
          placeholder="Enter secure token"
          @input="updateValues"
          class="form-control"
        />
        <p class="text-muted mt-5">
          This token is required for the node-shutdown webhook to accept
          requests. It will be stored in the values.yaml automatically.
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
