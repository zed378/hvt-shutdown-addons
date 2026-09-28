<script>
import { cronError } from './cron';

const STRATEGIES = ['migrate', 'stop', 'force'];

export default {
  name: 'NodeScheduleCard',

  props: {
    // { node, enabled, shutdownCron, poweronCron, vmStrategy,
    //   bmc: { protocol, host, port, user, password, verifyTls, hasPassword } }
    value: {
      type:     Object,
      required: true,
    },
    // The node's own (host) IP, shown so operators don't mix it up with the BMC IP.
    hostIp: {
      type:    String,
      default: '',
    },
    // async (node, bmc) => { ok, detail | error, ... }
    testConnection: {
      type:     Function,
      required: true,
    },
  },

  data() {
    return {
      strategies: STRATEGIES,
      testing:    false,
      testResult: null,
    };
  },

  computed: {
    bmc() {
      return this.value.bmc || {};
    },
    shutdownError() {
      return cronError(this.value.shutdownCron);
    },
    poweronError() {
      return cronError(this.value.poweronCron);
    },
    sameAsHostIp() {
      return !!this.bmc.host && this.bmc.host.trim() === this.hostIp;
    },
    poweronWithoutBmc() {
      return !!(this.value.poweronCron || '').trim() && !(this.bmc.host || '').trim();
    },
    defaultPort() {
      return this.bmc.protocol === 'redfish' ? 443 : 623;
    },
    statusText() {
      if (!this.value.enabled) {
        return 'not scheduled';
      }
      const parts = [];

      if ((this.value.shutdownCron || '').trim()) {
        parts.push(`shutdown ${ this.value.shutdownCron }`);
      }
      if ((this.value.poweronCron || '').trim()) {
        parts.push(`power on ${ this.value.poweronCron }`);
      }

      return parts.join(' | ') || 'enabled, no cron set';
    },
  },

  methods: {
    update(patch) {
      this.$emit('input', { ...this.value, ...patch });
    },
    updateBmc(patch) {
      this.testResult = null;
      this.update({ bmc: { ...this.bmc, ...patch } });
    },
    async test() {
      this.testing = true;
      this.testResult = null;
      try {
        this.testResult = await this.testConnection(this.value.node, this.bmc);
      } catch (e) {
        this.testResult = { ok: false, error: e?.message || String(e) };
      } finally {
        this.testing = false;
      }
    },
  },
};
</script>

<template>
  <div
    class="node-card"
    :class="{ enabled: value.enabled }"
  >
    <label class="head">
      <input
        type="checkbox"
        :checked="value.enabled"
        @change="update({ enabled: $event.target.checked })"
      />
      <span class="title">{{ value.node }}</span>
      <span
        v-if="hostIp"
        class="text-muted"
      >host IP {{ hostIp }}</span>
      <span class="text-muted status">{{ statusText }}</span>
    </label>

    <div
      v-if="value.enabled"
      class="body"
    >
      <div class="grid">
        <div>
          <label class="label">Shutdown schedule (cron)</label>
          <input
            :value="value.shutdownCron"
            type="text"
            spellcheck="false"
            placeholder="0 22 * * 5  (empty = none)"
            class="field"
            :class="{ invalid: shutdownError }"
            @input="update({ shutdownCron: $event.target.value })"
          />
          <p
            v-if="shutdownError"
            class="text-error hint"
          >
            {{ shutdownError }}
          </p>
        </div>
        <div>
          <label class="label">Power-on schedule (cron)</label>
          <input
            :value="value.poweronCron"
            type="text"
            spellcheck="false"
            placeholder="0 6 * * 1  (empty = none)"
            class="field"
            :class="{ invalid: poweronError }"
            @input="update({ poweronCron: $event.target.value })"
          />
          <p
            v-if="poweronError"
            class="text-error hint"
          >
            {{ poweronError }}
          </p>
        </div>
      </div>

      <label class="label mt-10">VMs on this node at shutdown</label>
      <select
        :value="value.vmStrategy"
        class="field"
        @change="update({ vmStrategy: $event.target.value })"
      >
        <option
          v-for="s in strategies"
          :key="s"
          :value="s"
        >
          {{ s }}
        </option>
      </select>
      <p class="text-muted hint">
        <b>migrate</b>: live-migrate to other nodes (else stop). <b>stop</b>: graceful stop — VMs stay off
        until their own power-on schedule. <b>force</b>: kill immediately.
      </p>

      <fieldset class="bmc mt-15">
        <legend>Management (BMC) connection — used for power-on</legend>
        <p class="text-muted hint mb-10">
          The iDRAC / iLO / XCC / IPMI management IP of this server. It is a different address from the node's host IP.
        </p>
        <div class="grid">
          <div>
            <label class="label">Protocol</label>
            <select
              :value="bmc.protocol || 'ipmi'"
              class="field"
              @change="updateBmc({ protocol: $event.target.value, port: '' })"
            >
              <option value="ipmi">
                IPMI over LAN
              </option>
              <option value="redfish">
                Redfish (HTTPS)
              </option>
            </select>
          </div>
          <div>
            <label class="label">Management IP</label>
            <input
              :value="bmc.host"
              type="text"
              spellcheck="false"
              placeholder="10.0.99.51"
              class="field"
              @input="updateBmc({ host: $event.target.value.trim() })"
            />
          </div>
          <div>
            <label class="label">Port</label>
            <input
              :value="bmc.port"
              type="number"
              min="1"
              max="65535"
              :placeholder="String(defaultPort)"
              class="field"
              @input="updateBmc({ port: $event.target.value })"
            />
          </div>
          <div>
            <label class="label">Username</label>
            <input
              :value="bmc.user"
              type="text"
              autocomplete="off"
              spellcheck="false"
              placeholder="admin"
              class="field"
              @input="updateBmc({ user: $event.target.value })"
            />
          </div>
          <div>
            <label class="label">Password</label>
            <input
              :value="bmc.password"
              type="password"
              autocomplete="new-password"
              :placeholder="bmc.hasPassword ? 'saved — leave empty to keep' : 'password'"
              class="field"
              @input="updateBmc({ password: $event.target.value })"
            />
          </div>
          <div v-if="bmc.protocol === 'redfish'">
            <label class="label">Certificate</label>
            <label class="checkbox">
              <input
                type="checkbox"
                :checked="!!bmc.verifyTls"
                @change="updateBmc({ verifyTls: $event.target.checked })"
              />
              Verify TLS certificate
            </label>
          </div>
        </div>

        <p
          v-if="sameAsHostIp"
          class="text-warning hint"
        >
          This is the node's host IP. Power-on needs the separate BMC management IP.
        </p>

        <div class="row mt-10">
          <button
            type="button"
            class="btn role-secondary"
            :disabled="testing || !bmc.host"
            @click="test"
          >
            {{ testing ? 'Testing…' : 'Test connection' }}
          </button>
          <span
            v-if="testResult && testResult.ok"
            class="text-success"
          >{{ testResult.detail }}<template v-if="testResult.testedFrom"> (from {{ testResult.testedFrom }})</template></span>
          <span
            v-else-if="testResult"
            class="text-error"
          >{{ testResult.error }}</span>
        </div>
      </fieldset>

      <p
        v-if="poweronWithoutBmc"
        class="text-warning hint mt-10"
      >
        A power-on schedule needs a management IP.
      </p>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.node-card {
  border: 1px solid var(--border, #ccc);
  border-radius: var(--border-radius, 4px);
  padding: 10px 14px;
  margin-top: 10px;

  &.enabled { border-color: var(--primary, #0055a4); }
  .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; cursor: pointer; }
  .title { font-weight: 600; font-size: 1.05em; }
  .status { margin-left: auto; }
  .body { margin-top: 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px 14px; }
  .label { display: block; font-weight: 600; margin-bottom: 4px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .field {
    width: 100%;
    padding: 7px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
    &.invalid { border-color: var(--error, #d32f2f); }
  }
  .bmc { border: 1px dashed var(--border, #ccc); border-radius: 4px; padding: 10px 12px; }
  .bmc legend { padding: 0 6px; font-weight: 600; }
  .checkbox { display: flex; gap: 6px; align-items: center; padding: 7px 0; cursor: pointer; }
  .hint { margin: 4px 0 0; font-size: 0.9em; }
  .mb-10 { margin-bottom: 10px; } .mt-10 { margin-top: 10px; } .mt-15 { margin-top: 15px; }
  input:focus-visible, select:focus-visible, button:focus-visible {
    outline: 2px solid var(--primary, #0055a4);
    outline-offset: 1px;
  }
}
</style>
