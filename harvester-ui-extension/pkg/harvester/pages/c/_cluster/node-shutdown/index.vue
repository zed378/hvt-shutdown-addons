<script>
import jsyaml from 'js-yaml';
import NodeShutdownCard from './NodeShutdownCard.vue';

const ADDON_TYPE = 'harvesterhci.io.addon';
const ADDON_ID = 'harvester-system/node-shutdown';
const STRATEGIES = ['migrate', 'stop', 'force'];
const VMI_TYPE = 'kubevirt.io.virtualmachineinstance';

function defaultCard(node) {
  return {
    node,
    nodeEnabled:        false,
    nodeCron:           '0 2 * * *',
    vmEnabled:          false,
    vmCron:             '0 1 * * *',
    vmStrategy:         'migrate',
    vms:                [],
    poweronNodeEnabled: false,
    bmcIp:              '',
    poweronNodeCron:    '0 6 * * 1-5',
    poweronVmEnabled:   false,
    poweronVmCron:      '0 6 * * 1-5',
    poweronVms:         [],
  };
}

export default {
  name: 'HarvesterNodeShutdown',

  components: { NodeShutdownCard },

  data() {
    return {
      addon:           null,
      token:           '',
      schedule:        { enabled: false, cron: '0 2 * * *', nodes: [], vmStrategy: 'migrate' },
      poweronSchedule: {
        enabled:        false,
        nodeCron:       '0 6 * * 1-5',
        vmCron:         '0 6 * * 1-5',
        nodes:          [],
        vms:            [],
        waitForReady:   true,
        timeoutSeconds: 300,
      },
      ipmiUser:     'admin',
      ipmiPassword: '',
      nodeCards:    [],
      vmisByNode:   {},
      nodeOptions:  [],
      strategies:   STRATEGIES,
      saving:       false,
      saved:        false,
      saveError:    '',
      loadError:    '',
      vmLoadError:  '',
      loading:      true,
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

      const p = parsed?.poweronSchedule || {};

      this.poweronSchedule = {
        enabled:        !!p.enabled,
        nodeCron:       p.nodeCron || '0 6 * * 1-5',
        vmCron:         p.vmCron || '0 6 * * 1-5',
        nodes:          Array.isArray(p.nodes) ? p.nodes : [],
        vms:            Array.isArray(p.vms) ? p.vms : [],
        waitForReady:   p.waitForReady !== false,
        timeoutSeconds: p.timeoutSeconds || 300,
      };
      this.ipmiUser = parsed?.ipmi?.user || 'admin';
      this.ipmiPassword = parsed?.ipmi?.password || '';

      this._savedNodeSchedules = Array.isArray(parsed?.nodeSchedules) ? parsed.nodeSchedules : [];
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
    // VMIs grouped by node for the per-node cards (best-effort).
    try {
      const vmis = await this.$store.dispatch('harvester/findAll', { type: VMI_TYPE });
      const byNode = {};

      (vmis || []).forEach((vmi) => {
        const nodeName = vmi?.status?.nodeName;
        const ns = vmi?.metadata?.namespace;
        const name = vmi?.metadata?.name;

        if (nodeName && ns && name) {
          (byNode[nodeName] = byNode[nodeName] || []).push(`${ ns }/${ name }`);
        }
      });
      Object.keys(byNode).forEach((k) => byNode[k].sort());
      this.vmisByNode = byNode;
    } catch (e) {
      this.vmisByNode = {};
      this.vmLoadError = e?.message || String(e);
    }
    // One card per known node: cluster nodes plus any node already saved.
    const savedByName = {};
    (this._savedNodeSchedules || []).forEach((c) => {
      if (c && c.node) {
        savedByName[c.node] = c;
      }
    });
    const names = [...new Set([...this.nodeOptions, ...Object.keys(savedByName)])].sort();

    this.nodeCards = names.map((node) => ({ ...defaultCard(node), ...(savedByName[node] || {}) }));
    this.loading = false;
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

    onCardInput(index, card) {
      this.$set(this.nodeCards, index, card);
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
        parsed.poweronSchedule = {
          enabled:        this.poweronSchedule.enabled,
          nodeCron:       this.poweronSchedule.nodeCron,
          vmCron:         this.poweronSchedule.vmCron,
          nodes:          this.poweronSchedule.nodes,
          vms:            this.poweronSchedule.vms,
          waitForReady:   this.poweronSchedule.waitForReady,
          timeoutSeconds: this.poweronSchedule.timeoutSeconds,
        };
        parsed.ipmi = {
          user:       this.ipmiUser || 'admin',
          password:   this.ipmiPassword || '',
          secretName: parsed?.ipmi?.secretName || 'node-poweron-ipmi',
        };
        parsed.nodeBmc = parsed.nodeBmc || {};
        this.nodeCards.forEach((c) => {
          if (c.bmcIp) {
            parsed.nodeBmc[c.node] = { bmcIp: c.bmcIp };
          }
        });
        parsed.nodeSchedules = this.nodeCards.map((c) => ({
          node:               c.node,
          nodeEnabled:        !!c.nodeEnabled,
          nodeCron:           c.nodeCron || '0 2 * * *',
          vmEnabled:          !!c.vmEnabled,
          vmCron:             c.vmCron || '0 1 * * *',
          vmStrategy:         STRATEGIES.includes(c.vmStrategy) ? c.vmStrategy : 'migrate',
          vms:                Array.isArray(c.vms) ? [...c.vms].sort() : [],
          poweronNodeEnabled: !!c.poweronNodeEnabled,
          bmcIp:              c.bmcIp || '',
          poweronNodeCron:    c.poweronNodeCron || '0 6 * * 1-5',
          poweronVmEnabled:   !!c.poweronVmEnabled,
          poweronVmCron:      c.poweronVmCron || '0 6 * * 1-5',
          poweronVms:         Array.isArray(c.poweronVms) ? [...c.poweronVms].sort() : [],
        }));
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

      <!-- Scheduled power-on -->
      <h3 class="mt-30">Scheduled power-on</h3>
      <label class="checkbox">
        <input v-model="poweronSchedule.enabled" type="checkbox" />
        Enable a scheduled power-on
      </label>

      <template v-if="poweronSchedule.enabled">
        <label class="label mt-15">Node cron schedule</label>
        <input
          v-model="poweronSchedule.nodeCron"
          type="text"
          spellcheck="false"
          placeholder="0 6 * * 1-5"
          class="field"
        />
        <p class="text-muted mt-5">Standard cron (UTC) for baremetal power-on via IPMI. Example: <code>0 6 * * 1-5</code> = 06:00 Mon-Fri.</p>

        <label class="label mt-15">VM cron schedule</label>
        <input
          v-model="poweronSchedule.vmCron"
          type="text"
          spellcheck="false"
          placeholder="0 6 * * 1-5"
          class="field"
        />
        <p class="text-muted mt-5">Standard cron (UTC) for starting VirtualMachines after nodes boot.</p>

        <label class="label mt-15">IPMI username</label>
        <input
          v-model="ipmiUser"
          type="text"
          placeholder="admin"
          class="field"
        />

        <label class="label mt-15">IPMI password</label>
        <input
          v-model="ipmiPassword"
          type="password"
          placeholder="Password"
          class="field"
        />
        <p class="text-muted mt-5">Credentials to access server BMCs via IPMI over LAN.</p>

        <label class="checkbox mt-15">
          <input v-model="poweronSchedule.waitForReady" type="checkbox" />
          Wait for nodes to become Ready in Kubernetes before powering on VMs
        </label>
      </template>

      <!-- Per-node schedules: one node cron plus one VM cron per card -->
      <h3 class="mt-30">Per-node schedules</h3>
      <p class="text-muted mb-10">
        One card per node. Each card holds a node shutdown schedule and a single VM schedule
        with selectable VMs. Nothing checked means all VMs on that node.
      </p>
      <p
        v-if="vmLoadError"
        class="text-warning mb-10"
      >Could not list VMs: {{ vmLoadError }}. VM checkboxes are empty until the list loads.</p>
      <p
        v-if="loading"
        class="text-muted"
      >Loading nodes and virtual machines...</p>
      <p
        v-else-if="!nodeCards.length"
        class="text-muted"
      >No nodes found. Cards appear once the cluster node list loads.</p>
      <NodeShutdownCard
        v-for="(card, i) in nodeCards"
        :key="card.node"
        :value="card"
        :vm-options="vmisByNode[card.node] || []"
        @input="onCardInput(i, $event)"
      />

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
