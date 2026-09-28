<script>
import jsyaml from 'js-yaml';
import NodeScheduleCard from './NodeScheduleCard.vue';
import { cronError, CRON_EXAMPLES } from './cron';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/node-shutdown';
const VM_TYPE = 'kubevirt.io.virtualmachine';
const VMI_TYPE = 'kubevirt.io.virtualmachineinstance';
const STRATEGIES = ['migrate', 'stop', 'force'];

function nodeDefaults(node) {
  return {
    node,
    enabled:      false,
    shutdownCron: '',
    poweronCron:  '',
    vmStrategy:   'migrate',
    bmc:          {
      protocol: 'ipmi', host: '', port: '', user: '', password: '', verifyTls: false, hasPassword: false
    },
  };
}

function vmDefaults(vm) {
  return {
    vm, enabled: false, shutdownCron: '', poweronCron: ''
  };
}

// Seed the new per-node / per-VM model from the older `nodeSchedules` cards the
// first time this page is opened, so existing schedules are not lost.
function migrateLegacy(legacy) {
  const nodes = [];
  const vms = [];

  (legacy || []).forEach((c) => {
    if (!c?.node) {
      return;
    }
    const shutdownCron = c.nodeEnabled ? c.nodeCron || '' : '';
    const poweronCron = c.poweronNodeEnabled ? c.poweronNodeCron || '' : '';

    nodes.push({
      node:       c.node,
      enabled:    !!(shutdownCron || poweronCron),
      shutdownCron,
      poweronCron,
      vmStrategy: STRATEGIES.includes(c.vmStrategy) ? c.vmStrategy : 'migrate',
    });
    const off = c.vmEnabled ? (c.vms || []) : [];
    const on = c.poweronVmEnabled ? (c.poweronVms || []) : [];

    [...new Set([...off, ...on])].forEach((vm) => vms.push({
      vm,
      enabled:      true,
      shutdownCron: off.includes(vm) ? c.vmCron || '' : '',
      poweronCron:  on.includes(vm) ? c.poweronVmCron || '' : '',
    }));
  });

  return { nodes, vms };
}

function b64decode(s) {
  try {
    return atob(s || '');
  } catch (e) {
    return '';
  }
}

export default {
  name: 'HarvesterPowerSchedules',

  components: { NodeScheduleCard },

  data() {
    return {
      addon:         null,
      values:        {},
      token:         '',
      timeZone:      '',
      nodeCards:     [],
      vmRows:        [],
      hostIps:       {},
      vmFilter:      '',
      examples:      CRON_EXAMPLES,
      migrated:      false,
      saving:        false,
      saved:         false,
      saveError:     '',
      loadError:     '',
      vmLoadError:   '',
      tokenWarning:  '',
      loading:       true,
    };
  },

  async fetch() {
    let parsed = {};

    try {
      this.addon = await this.$store.dispatch('harvester/find', { type: ADDON_TYPE, id: ADDON_ID });
      parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};
    } catch (e) {
      this.loadError = e?.message || String(e);
      this.loading = false;

      return;
    }
    this.values = parsed;
    this.timeZone = parsed.scheduleTimeZone || '';

    // Token for the "Test connection" call: the Secret is the source of truth
    // (the token console may have rotated it); fall back to the add-on value.
    try {
      const ns = parsed?.secret?.namespace || 'harvester-system';
      const name = parsed?.secret?.name || 'node-shutdown-auth';
      const key = parsed?.secret?.key || 'auth-token';
      const secret = await this.$store.dispatch('harvester/find', { type: 'secret', id: `${ ns }/${ name }` });

      this.token = b64decode(secret?.data?.[key]);
    } catch (e) {}
    if (!this.token) {
      this.token = parsed?.auth?.token || '';
    }
    if (!this.token) {
      this.tokenWarning = 'Could not read the auth token, so Test connection is unavailable. Set the token on the Node Shutdown page.';
    }

    // Nodes (and their host IPs, to contrast with the BMC IP).
    let nodeNames = [];

    try {
      const nodes = await this.$store.dispatch('harvester/findAll', { type: 'node' });

      (nodes || []).forEach((n) => {
        const name = n.metadata?.name;

        if (name) {
          nodeNames.push(name);
          const ip = (n.status?.addresses || []).find((a) => a.type === 'InternalIP');

          this.$set(this.hostIps, name, ip?.address || '');
        }
      });
    } catch (e) {}

    // VMs (running or not) and where they currently run.
    const vmInfo = {};

    try {
      const vms = await this.$store.dispatch('harvester/findAll', { type: VM_TYPE });

      (vms || []).forEach((vm) => {
        const ref = `${ vm.metadata?.namespace }/${ vm.metadata?.name }`;

        vmInfo[ref] = { status: vm.status?.printableStatus || (vm.status?.ready ? 'Running' : 'Stopped'), node: '' };
      });
      const vmis = await this.$store.dispatch('harvester/findAll', { type: VMI_TYPE });

      (vmis || []).forEach((vmi) => {
        const ref = `${ vmi.metadata?.namespace }/${ vmi.metadata?.name }`;

        if (vmInfo[ref]) {
          vmInfo[ref].node = vmi.status?.nodeName || '';
        }
      });
    } catch (e) {
      this.vmLoadError = e?.message || String(e);
    }

    let savedNodes = Array.isArray(parsed.nodePowerSchedules) ? parsed.nodePowerSchedules : [];
    let savedVms = Array.isArray(parsed.vmPowerSchedules) ? parsed.vmPowerSchedules : [];

    if (!savedNodes.length && !savedVms.length && (parsed.nodeSchedules || []).length) {
      const legacy = migrateLegacy(parsed.nodeSchedules);

      savedNodes = legacy.nodes;
      savedVms = legacy.vms;
      this.migrated = !!(savedNodes.some((n) => n.enabled) || savedVms.length);
    }

    const nodeByName = {};

    savedNodes.forEach((n) => {
      if (n?.node) {
        nodeByName[n.node] = n;
      }
    });
    nodeNames = [...new Set([...nodeNames, ...Object.keys(nodeByName)])].sort();
    const nodeBmc = parsed.nodeBmc || {};

    this.nodeCards = nodeNames.map((node) => {
      const saved = nodeByName[node] || {};
      const b = nodeBmc[node] || {};

      return {
        ...nodeDefaults(node),
        ...saved,
        vmStrategy: STRATEGIES.includes(saved.vmStrategy) ? saved.vmStrategy : 'migrate',
        bmc:        {
          protocol:    b.protocol || 'ipmi',
          host:        b.host || b.bmcIp || saved.bmcIp || '',
          port:        b.port || '',
          user:        b.user || '',
          password:    '', // never echoed back to the browser
          verifyTls:   !!b.verifyTls,
          hasPassword: !!b.password,
        },
      };
    });

    const vmByRef = {};

    savedVms.forEach((v) => {
      if (v?.vm) {
        vmByRef[v.vm] = v;
      }
    });
    const refs = [...new Set([...Object.keys(vmInfo), ...Object.keys(vmByRef)])].sort();

    this.vmRows = refs.map((vm) => ({
      ...vmDefaults(vm),
      ...(vmByRef[vm] || {}),
      status:  vmInfo[vm]?.status || 'Not found',
      node:    vmInfo[vm]?.node || '',
      missing: !vmInfo[vm] && !this.vmLoadError,
    }));
    this.loading = false;
  },

  computed: {
    filteredVms() {
      const q = this.vmFilter.trim().toLowerCase();

      return q ? this.vmRows.filter((r) => r.vm.toLowerCase().includes(q) || (r.node || '').includes(q)) : this.vmRows;
    },
    enabledNodes() {
      return this.nodeCards.filter((c) => c.enabled).length;
    },
    enabledVms() {
      return this.vmRows.filter((r) => r.enabled).length;
    },
    problems() {
      const out = [];

      this.nodeCards.filter((c) => c.enabled).forEach((c) => {
        if (cronError(c.shutdownCron) || cronError(c.poweronCron)) {
          out.push(`Node ${ c.node }: invalid cron`);
        }
        if ((c.poweronCron || '').trim() && !(c.bmc.host || '').trim()) {
          out.push(`Node ${ c.node }: power-on needs a management IP`);
        }
      });
      this.vmRows.filter((r) => r.enabled).forEach((r) => {
        if (cronError(r.shutdownCron) || cronError(r.poweronCron)) {
          out.push(`VM ${ r.vm }: invalid cron`);
        }
      });

      return out;
    },
    apiBase() {
      const v = this.values || {};
      const ns = v.daemonset?.namespace || 'harvester-system';
      const svc = `${ v.daemonset?.name || 'node-shutdown-webhook' }-nodeport`;
      const port = v.nodePort?.port || 30088;
      const scheme = v.tls?.enabled ? 'https' : 'http';

      return `api/v1/namespaces/${ ns }/services/${ scheme }:${ svc }:${ port }/proxy`;
    },
  },

  methods: {
    cronError,

    onNodeInput(index, card) {
      this.$set(this.nodeCards, index, card);
    },

    updateVm(row, patch) {
      const i = this.vmRows.indexOf(row);

      this.$set(this.vmRows, i, { ...row, ...patch });
    },

    // Calls the API through the Kubernetes service proxy. The proxy consumes the
    // Authorization header, so the token goes in X-Node-Shutdown-Token.
    async testConnection(node, bmc) {
      if (!this.token) {
        return { ok: false, error: 'No auth token available' };
      }
      const url = this.$store.getters['harvester-common/getHarvesterClusterUrl'](`${ this.apiBase }/system/bmc/test`);

      try {
        const res = await this.$store.dispatch('harvester/request', {
          url,
          method:  'POST',
          headers: { 'Content-Type': 'application/json', 'X-Node-Shutdown-Token': this.token },
          data:    {
            node,
            protocol:  bmc.protocol,
            host:      bmc.host,
            port:      bmc.port ? Number(bmc.port) : undefined,
            user:      bmc.user,
            password:  bmc.password,
            verifyTls: !!bmc.verifyTls,
          },
        });

        return res?.data || res;
      } catch (e) {
        const status = e?._status || e?.status;

        if (status === 401) {
          return { ok: false, error: 'The service rejected the auth token (401).' };
        }
        if (status === 503 || status === 502 || status === 404) {
          return { ok: false, error: `Shutdown service not reachable through the cluster proxy (HTTP ${ status }). Is the add-on enabled?` };
        }

        return { ok: false, error: e?.message || e?.detail || `Request failed (${ status || 'unknown' })` };
      }
    },

    async save() {
      this.saving = true;
      this.saved = false;
      this.saveError = '';
      try {
        const parsed = jsyaml.load(this.addon?.spec?.valuesContent || '') || {};
        const nodeBmc = parsed.nodeBmc || {};

        this.nodeCards.forEach((c) => {
          const b = c.bmc || {};
          const prev = nodeBmc[c.node] || {};

          if (!(b.host || '').trim()) {
            delete nodeBmc[c.node];

            return;
          }
          const entry = {
            protocol:  b.protocol || 'ipmi',
            host:      b.host.trim(),
            user:      b.user || '',
            password:  b.password || prev.password || '',
            verifyTls: !!b.verifyTls,
          };

          if (b.port) {
            entry.port = Number(b.port);
          }
          nodeBmc[c.node] = entry;
        });
        parsed.nodeBmc = nodeBmc;

        parsed.nodePowerSchedules = this.nodeCards
          .filter((c) => c.enabled || (c.shutdownCron || '').trim() || (c.poweronCron || '').trim())
          .map((c) => ({
            node:         c.node,
            enabled:      !!c.enabled,
            shutdownCron: (c.shutdownCron || '').trim(),
            poweronCron:  (c.poweronCron || '').trim(),
            vmStrategy:   STRATEGIES.includes(c.vmStrategy) ? c.vmStrategy : 'migrate',
          }));
        parsed.vmPowerSchedules = this.vmRows
          .filter((r) => r.enabled || (r.shutdownCron || '').trim() || (r.poweronCron || '').trim())
          .map((r) => ({
            vm:           r.vm,
            enabled:      !!r.enabled,
            shutdownCron: (r.shutdownCron || '').trim(),
            poweronCron:  (r.poweronCron || '').trim(),
          }));
        parsed.scheduleTimeZone = this.timeZone.trim();
        // Superseded by the per-node / per-VM lists above; clearing avoids
        // duplicate CronJobs from the old per-node cards.
        parsed.nodeSchedules = [];

        this.addon.spec.valuesContent = jsyaml.dump(parsed);
        await this.addon.save();
        this.nodeCards = this.nodeCards.map((c) => ({
          ...c,
          bmc: {
            ...c.bmc, password: '', hasPassword: !!nodeBmc[c.node]?.password
          }
        }));
        this.migrated = false;
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
  <div class="power-schedules">
    <h1 class="mb-10">
      Power Schedules
    </h1>

    <div
      v-if="loadError"
      class="banner-error mb-20"
    >
      Could not load the <code>node-shutdown</code> add-on: <strong>{{ loadError }}</strong>
    </div>

    <template v-else>
      <p class="text-muted mb-10">
        Check a node or VM to schedule it. Each one has its own <b>shutdown</b> and <b>power-on</b> cron.
        Leave a cron empty to skip that direction. Nodes power on through their BMC (IPMI / Redfish).
        VMs are stopped and started with <code>kubectl</code>.
      </p>
      <p class="text-muted mb-10">
        Cron format: <code>minute hour day month weekday</code>. Examples:
        <span
          v-for="e in examples"
          :key="e.cron"
          class="example"
        ><code>{{ e.cron }}</code> {{ e.text }}</span>
      </p>

      <div class="row mb-10">
        <label
          class="label inline"
          for="tz"
        >Time zone</label>
        <input
          id="tz"
          v-model="timeZone"
          type="text"
          spellcheck="false"
          placeholder="UTC (e.g. Asia/Jakarta)"
          class="field tz"
        />
      </div>

      <div
        v-if="migrated"
        class="banner-info mb-10"
      >
        Imported your existing per-node schedules. Review them, then save to switch over.
      </div>
      <p
        v-if="tokenWarning"
        class="text-warning"
      >
        {{ tokenWarning }}
      </p>

      <h3 class="mt-20">
        Nodes <span class="text-muted count">{{ enabledNodes }} scheduled</span>
      </h3>
      <p class="text-warning hint">
        Power-on runs from a node that is still up. If every node is off, nothing in the cluster can power them back on.
        Keep at least one node running, or power on from outside the cluster.
      </p>
      <p
        v-if="loading"
        class="text-muted"
      >
        Loading nodes and virtual machines…
      </p>
      <NodeScheduleCard
        v-for="(card, i) in nodeCards"
        :key="card.node"
        :value="card"
        :host-ip="hostIps[card.node] || ''"
        :test-connection="testConnection"
        @input="onNodeInput(i, $event)"
      />

      <h3 class="mt-30">
        Virtual machines <span class="text-muted count">{{ enabledVms }} scheduled</span>
      </h3>
      <p
        v-if="vmLoadError"
        class="text-warning"
      >
        Could not list VMs: {{ vmLoadError }}
      </p>
      <input
        v-model="vmFilter"
        type="search"
        placeholder="Filter by name, namespace or node"
        class="field mb-10"
      />
      <div
        v-if="!loading && !vmRows.length"
        class="text-muted"
      >
        No virtual machines found.
      </div>
      <div
        v-for="row in filteredVms"
        :key="row.vm"
        class="vm-row"
        :class="{ enabled: row.enabled }"
      >
        <label class="head">
          <input
            type="checkbox"
            :checked="row.enabled"
            @change="updateVm(row, { enabled: $event.target.checked })"
          />
          <span class="title">{{ row.vm }}</span>
          <span class="text-muted">{{ row.status }}<template v-if="row.node"> on {{ row.node }}</template></span>
          <span
            v-if="row.missing"
            class="text-warning"
          >VM no longer exists</span>
        </label>
        <div
          v-if="row.enabled"
          class="grid mt-10"
        >
          <div>
            <label class="label">Shutdown schedule (cron)</label>
            <input
              :value="row.shutdownCron"
              type="text"
              spellcheck="false"
              placeholder="0 21 * * *  (empty = none)"
              class="field"
              :class="{ invalid: cronError(row.shutdownCron) }"
              @input="updateVm(row, { shutdownCron: $event.target.value })"
            />
            <p
              v-if="cronError(row.shutdownCron)"
              class="text-error hint"
            >
              {{ cronError(row.shutdownCron) }}
            </p>
          </div>
          <div>
            <label class="label">Power-on schedule (cron)</label>
            <input
              :value="row.poweronCron"
              type="text"
              spellcheck="false"
              placeholder="0 7 * * *  (empty = none)"
              class="field"
              :class="{ invalid: cronError(row.poweronCron) }"
              @input="updateVm(row, { poweronCron: $event.target.value })"
            />
            <p
              v-if="cronError(row.poweronCron)"
              class="text-error hint"
            >
              {{ cronError(row.poweronCron) }}
            </p>
          </div>
        </div>
      </div>

      <div
        v-if="problems.length"
        class="banner-error mt-20"
      >
        <div
          v-for="p in problems"
          :key="p"
        >
          {{ p }}
        </div>
      </div>
      <button
        type="button"
        class="btn role-primary mt-20"
        :disabled="saving || loading || !!problems.length"
        @click="save"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
      <p
        v-if="saved"
        class="text-success mt-10"
      >
        Saved. The schedule CronJobs update when the add-on redeploys (about a minute).
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
.power-schedules {
  padding: 20px;
  max-width: 860px;

  h3 { margin-bottom: 4px; }
  .count { font-size: 0.8em; font-weight: normal; margin-left: 6px; }
  .label { display: block; font-weight: 600; margin-bottom: 4px; }
  .label.inline { margin: 0; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 14px; }
  .field {
    width: 100%;
    padding: 7px 10px;
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    background: var(--input-bg, transparent);
    color: var(--input-text, inherit);
    &.invalid { border-color: var(--error, #d32f2f); }
    &.tz { width: auto; min-width: 240px; flex: 1; max-width: 320px; }
  }
  .example { margin-right: 12px; white-space: nowrap; }
  .hint { margin: 4px 0; font-size: 0.9em; }
  .vm-row {
    border: 1px solid var(--border, #ccc);
    border-radius: var(--border-radius, 4px);
    padding: 8px 14px;
    margin-top: 6px;
    &.enabled { border-color: var(--primary, #0055a4); }
    .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; cursor: pointer; }
    .title { font-weight: 600; overflow-wrap: anywhere; }
  }
  .mb-10 { margin-bottom: 10px; } .mb-20 { margin-bottom: 20px; }
  .mt-10 { margin-top: 10px; } .mt-20 { margin-top: 20px; } .mt-30 { margin-top: 30px; }
  .banner-error {
    padding: 10px 12px; border-radius: 6px;
    background: rgba(200, 0, 0, 0.1); border: 1px solid rgba(200, 0, 0, 0.3);
  }
  .banner-info {
    padding: 10px 12px; border-radius: 6px;
    background: rgba(0, 85, 164, 0.08); border: 1px solid rgba(0, 85, 164, 0.3);
  }
  input:focus-visible, select:focus-visible, button:focus-visible {
    outline: 2px solid var(--primary, #0055a4);
    outline-offset: 1px;
  }
}
</style>
