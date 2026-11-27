<script>
import cronstrue from 'cronstrue';
import 'cronstrue/locales/id';
import { cronError } from './cron';
import AlertSettings from './AlertSettings.vue';

export function rotationDefaults() {
  return {
    enabled:               false,
    count:                 1,
    migrateCron:           '0 18 * * *',
    deadlineCron:          '0 20 * * *',
    poweronCron:           '0 6 * * *',
    exclude:               [],
    neverOff:              [],
    maxControlPlaneOff:    1,
    maxNodesOff:           3,
    headroomPercent:       15,
    pinnedVms:             'move',
    stopVms:               [],
    poweronTimeoutSeconds: 900,
  };
}

export function returnHomeDefaults() {
  return {
    enabled:        true,
    maxParallel:    2,
    timeoutSeconds: 900
  };
}

function dailyMinutes(cron) {
  const match = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec((cron || '').trim());

  return match ? Number(match[2]) * 60 + Number(match[1]) : null;
}

export function rotationProblems(rotation, home) {
  const out = [];

  ['migrateCron', 'deadlineCron', 'poweronCron'].forEach((key) => {
    if (!(rotation[key] || '').trim() || cronError(rotation[key])) {
      out.push(`Cron ${ key } harus berisi jadwal yang valid (menit jam tanggal bulan hari).`);
    }
  });
  const migrate = dailyMinutes(rotation.migrateCron);
  const deadline = dailyMinutes(rotation.deadlineCron);
  const poweron = dailyMinutes(rotation.poweronCron);

  if (migrate !== null && deadline !== null && deadline <= migrate) {
    out.push('Batas pemadaman harus setelah migrasi pada hari yang sama.');
  }
  // Power-on may be on the following morning, as in the default 18:00/20:00/06:00 cycle.
  if (migrate !== null && deadline !== null && poweron !== null &&
      (poweron - migrate + 1440) % 1440 <= deadline - migrate) {
    out.push('Waktu menyalakan harus setelah batas pemadaman dan sebelum migrasi berikutnya.');
  }
  const numbers = [
    [rotation.count, 1, 3, 'Jumlah node'],
    [rotation.maxNodesOff, 1, null, 'Batas node mati'],
    [rotation.headroomPercent, 0, 100, 'Cadangan kapasitas'],
    [rotation.poweronTimeoutSeconds, 1, null, 'Batas tunggu node'],
    [home.maxParallel, 1, null, 'VM pulang bersamaan'],
    [home.timeoutSeconds, 1, null, 'Batas tunggu VM'],
  ];

  numbers.forEach(([value, min, max, label]) => {
    if (!Number.isInteger(value) || value < min || (max !== null && value > max)) {
      out.push(`${ label } harus bilangan bulat ${ max === null ? `minimal ${ min }` : `${ min }–${ max }` }.`);
    }
  });
  if (!['move', 'skipNode', 'stop'].includes(rotation.pinnedVms)) {
    out.push('Pilih tindakan untuk VM yang terikat node.');
  }

  return out;
}

export default {
  name:       'RotationTab',
  components: { AlertSettings },
  props:      {
    rotation:   {
      type:     Object,
      required: true
    },
    returnHome: {
      type:     Object,
      required: true
    },
    nodes:      {
      type:    Array,
      default: () => []
    },
    vms:        {
      type:    Array,
      default: () => []
    },
    request:    {
      type:     Function,
      required: true
    },
  },
  emits: ['update-rotation', 'update-return-home'],
  data() {
    return {
      status:     {
        nodes:   {},
        history: [],
        round:   null
      },
      plan:       null,
      busy:       false,
      error:      '',
      message:    '',
      cronFields: [
        {
          key:   'migrateCron',
          label: 'Mulai migrasi (cron)'
        },
        {
          key:   'deadlineCron',
          label: 'Batas pemadaman (cron)'
        },
        {
          key:   'poweronCron',
          label: 'Nyalakan kembali (cron)'
        },
      ],
      numberFields: [
        {
          key:   'count',
          label: 'Jumlah node per putaran',
          min:   1,
          max:   3
        },
        {
          key:   'headroomPercent',
          label: 'Cadangan kapasitas (%)',
          min:   0,
          max:   100
        },
        {
          key:   'poweronTimeoutSeconds',
          label: 'Batas tunggu node siap (detik)',
          min:   1
        },
        {
          key:   'maxNodesOff',
          label: 'Maksimum node mati',
          min:   1
        },
      ],
    };
  },
  computed: {
    problems() {
      return rotationProblems(this.rotation, this.returnHome);
    },
    nodeRows() {
      const live = Object.fromEntries(this.nodes.map((n) => [n.metadata.name, n]));
      const names = [...new Set([
        ...Object.keys(live), ...Object.keys(this.status.nodes || {}),
        ...this.rotation.exclude, ...this.rotation.neverOff,
      ])].sort();

      return names.map((name) => {
        const node = live[name] || {};
        const labels = node.metadata?.labels || {};
        const resources = {
          ...node.status?.capacity,
          ...node.status?.allocatable
        };
        const planned = this.plan?.nodes?.find((n) => n.name === name);

        return {
          name,
          controlPlane: String(labels['node-role.kubernetes.io/control-plane']) === 'true',
          gpu:          !!planned?.gpu || Object.keys(resources).some((key) => /gpu/i.test(key) && Number(resources[key]) > 0) ||
            Object.keys(labels).some((key) => /gpu/i.test(key) && /^(true|[1-9]\d*)$/.test(String(labels[key]))),
          ...this.status.nodes?.[name],
        };
      });
    },
  },
  watch: {
    rotation: {
      deep: true,
      handler() {
        this.plan = null;
      }
    },
  },
  mounted() {
    this.refresh();
  },
  methods: {
    update(patch) {
      this.$emit('update-rotation', {
        ...this.rotation,
        ...patch
      });
    },
    updateHome(patch) {
      this.$emit('update-return-home', {
        ...this.returnHome,
        ...patch
      });
    },
    setMembership(key, name, checked) {
      const members = this.rotation[key].filter((n) => n !== name);

      this.update({ [key]: checked ? [...members, name] : members });
    },
    cronText(expr) {
      if (!expr || cronError(expr)) {
        return 'Cron tidak valid: gunakan menit jam tanggal bulan hari.';
      }
      try {
        return cronstrue.toString(expr, { locale: 'id', use24HourTimeFormat: true });
      } catch {
        return 'Jadwal khusus; urutan waktu tidak diperiksa otomatis.';
      }
    },
    async refresh() {
      this.busy = true;
      this.error = '';
      try {
        this.status = await this.request('/system/rotation/status');
      } catch (e) {
        this.error = 'Gagal memuat status rotasi. Periksa layanan dan token akses.';
      } finally {
        this.busy = false;
      }
    },
    async preview() {
      if (this.problems.length) {
        return;
      }
      this.busy = true;
      this.error = '';
      this.plan = null;
      const config = JSON.parse(JSON.stringify(this.rotation));

      try {
        const plan = await this.request('/system/rotation/preview', 'POST', config);

        if (JSON.stringify(config) === JSON.stringify(this.rotation)) {
          this.plan = plan;
        }
      } catch (e) {
        this.error = 'Gagal menghitung putaran berikutnya. Periksa layanan dan konfigurasi.';
      } finally {
        this.busy = false;
      }
    },
    async nodeAction(node, action) {
      this.busy = true;
      this.error = '';
      this.message = '';
      try {
        const result = await this.request(action === 'reset' ? '/system/rotation/reset' : '/system/return-home', 'POST', { node });

        if (result?.ok === false) {
          throw new Error();
        }
        this.message = action === 'reset' ? `Status gagal ${ node } direset.` : `${ node }: ${ (result.returned || []).length } VM dipulangkan; ${ Object.keys(result.failed || {}).length } gagal.`;
        this.plan = null;
        this.status = await this.request('/system/rotation/status');
      } catch (e) {
        this.error = 'Tindakan atau pemuatan ulang status gagal. Muat ulang status sebelum mencoba lagi.';
      } finally {
        this.busy = false;
      }
    },
    nodeTimes(round, key) {
      return (round.nodes || []).map((n) => `${ n.name }: ${ n[key] || '—' }`).join('; ');
    },
    total(round, key) {
      return (round.nodes || []).reduce((sum, n) => sum + (n[key] || []).length, 0);
    },
    phase(value) {
      return ({
        ok:         'Baik',
        failed:     'Gagal',
        partial:    'Sebagian',
        cancelled:  'Dibatalkan',
        draining:   'Migrasi',
        off:        'Mati',
        poweringOn: 'Menyalakan',
        done:       'Selesai'
      })[value] || '—';
    },
  },
};
</script>

<template>
  <section class="rotation-tab">
    <h3>Pengaturan rotasi</h3>
    <label class="checkbox"><input
      :checked="rotation.enabled"
      type="checkbox"
      @change="update({ enabled: $event.target.checked })"
    > Aktifkan rotasi node</label>
    <p class="text-muted">
      Cron mengikuti zona waktu halaman ini. Menyalakan kembali dapat dijadwalkan keesokan pagi.
    </p>
    <div class="grid">
      <label
        v-for="field in numberFields"
        :key="field.key"
      >
        <span class="label">{{ field.label }}</span>
        <input
          :value="rotation[field.key]"
          type="number"
          :min="field.min"
          :max="field.max"
          step="1"
          class="field"
          @input="update({ [field.key]: $event.target.value === '' ? null : Number($event.target.value) })"
        >
      </label>
      <label
        v-for="field in cronFields"
        :key="field.key"
      >
        <span class="label">{{ field.label }}</span>
        <input
          :value="rotation[field.key]"
          type="text"
          spellcheck="false"
          class="field"
          @input="update({ [field.key]: $event.target.value })"
        >
        <span class="text-muted hint">{{ cronText(rotation[field.key]) }}</span>
      </label>
      <label>
        <span class="label">VM yang terikat node</span>
        <select
          :value="rotation.pinnedVms"
          class="field"
          @change="update({ pinnedVms: $event.target.value })"
        >
          <option value="move">Lepas pin, pindahkan, pasang lagi saat pulang</option>
          <option value="skipNode">Lewati node</option>
          <option value="stop">Hentikan VM</option>
        </select>
      </label>
    </div>
    <label>
      <span class="label">VM yang dimatikan selama rotasi</span>
      <select
        :value="rotation.stopVms || []"
        multiple
        size="6"
        class="field"
        @change="update({ stopVms: [...$event.target.selectedOptions].map((o) => o.value) })"
      >
        <option
          v-for="vm in [...new Set([...vms, ...(rotation.stopVms || [])])].sort()"
          :key="vm"
          :value="vm"
          :selected="(rotation.stopVms || []).includes(vm)"
        >{{ vm }}</option>
      </select>
      <span class="text-muted hint">Dihentikan saat migrasi mulai, dinyalakan lagi saat node menyala. VM dengan jadwal shutdown per-VM aktif sudah tidak dihitung dalam cek kapasitas.</span>
    </label>
    <h3>Pulangkan VM</h3>
    <label class="checkbox"><input
      :checked="returnHome.enabled"
      type="checkbox"
      @change="updateHome({ enabled: $event.target.checked })"
    > Pulangkan VM otomatis setelah node siap</label>
    <div class="grid">
      <label><span class="label">Maksimum VM pulang bersamaan</span><input
        :value="returnHome.maxParallel"
        type="number"
        min="1"
        step="1"
        class="field"
        @input="updateHome({ maxParallel: $event.target.value === '' ? null : Number($event.target.value) })"
      ></label>
      <label><span class="label">Batas tunggu VM (detik)</span><input
        :value="returnHome.timeoutSeconds"
        type="number"
        min="1"
        step="1"
        class="field"
        @input="updateHome({ timeoutSeconds: $event.target.value === '' ? null : Number($event.target.value) })"
      ></label>
    </div>
    <p class="text-muted">
      Gunakan tombol Simpan di bawah untuk menyimpan pengaturan rotasi dan pemulangan VM.
    </p>
    <div
      v-if="problems.length"
      class="text-error"
      role="alert"
    >
      <p
        v-for="problem in problems"
        :key="problem"
      >
        {{ problem }}
      </p>
    </div>
    <h3>Node rotasi</h3>
    <button
      type="button"
      class="btn role-secondary"
      :disabled="busy"
      @click="refresh"
    >
      {{ busy ? 'Memuat…' : 'Muat ulang status' }}
    </button>
    <p v-if="status.round">
      Putaran {{ status.round.id }}: {{ phase(status.round.phase) }}. {{ status.round.message }}
    </p>
    <p
      v-if="error"
      class="text-error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="message"
      role="status"
    >
      {{ message }}
    </p>
    <div class="table-scroll">
      <table>
        <caption class="text-muted">
          Keikutsertaan dan status pemadaman node
        </caption>
        <thead><tr><th>Nama</th><th>Peran</th><th>Ikut rotasi</th><th>Tidak pernah mati</th><th>Terakhir mati</th><th>Status</th><th>Tindakan</th></tr></thead>
        <tbody>
          <tr
            v-for="node in nodeRows"
            :key="node.name"
          >
            <td>{{ node.name }}</td>
            <td>
              <span
                v-if="node.controlPlane"
                class="badge"
              >Control-plane</span> <span
                v-if="node.gpu"
                class="badge"
              >GPU</span>
            </td>
            <td>
              <input
                type="checkbox"
                :aria-label="`Ikut rotasi: ${ node.name }`"
                :checked="!rotation.exclude.includes(node.name)"
                @change="setMembership('exclude', node.name, !$event.target.checked)"
              >
            </td>
            <td>
              <input
                type="checkbox"
                :aria-label="`Tidak pernah mati: ${ node.name }`"
                :checked="rotation.neverOff.includes(node.name)"
                @change="setMembership('neverOff', node.name, $event.target.checked)"
              >
            </td>
            <td>{{ node.lastOffAt || '—' }}</td><td>{{ phase(node.status) }}</td>
            <td class="actions">
              <button
                v-if="node.status === 'failed'"
                type="button"
                class="btn role-secondary"
                :disabled="busy"
                @click="nodeAction(node.name, 'reset')"
              >
                Reset
              </button>
              <button
                type="button"
                class="btn role-secondary"
                :disabled="busy"
                @click="nodeAction(node.name, 'return')"
              >
                Pulangkan VM
              </button>
            </td>
          </tr>
          <tr v-if="!nodeRows.length">
            <td colspan="7">
              Belum ada node.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <h3>Putaran berikutnya</h3>
    <button
      type="button"
      class="btn role-secondary"
      :disabled="busy || !!problems.length"
      @click="preview"
    >
      Pratinjau konfigurasi saat ini
    </button>
    <div
      v-if="plan"
      aria-live="polite"
    >
      <p>Terpilih: {{ (plan.selected || []).join(', ') || 'Tidak ada' }}</p>
      <p
        v-if="plan.reduced"
        class="text-warning"
      >
        Jumlah node dikurangi karena batas kapasitas atau kelayakan.
      </p>
      <p>Dilewati: {{ Object.keys(plan.skipped || {}).length ? '' : 'Tidak ada' }}</p>
      <ul>
        <li
          v-for="(reason, node) in plan.skipped"
          :key="node"
        >
          {{ node }}: {{ reason }}
        </li>
      </ul>
      <template v-if="plan.capacity">
        <p>Memori dibutuhkan / tersedia: {{ plan.capacity.memoryNeeded }} / {{ plan.capacity.memoryFree }} byte.</p>
        <p>CPU dibutuhkan / tersedia: {{ plan.capacity.cpuNeeded }} / {{ plan.capacity.cpuFree }} millicore.</p>
        <p>Kapasitas mencukupi: {{ plan.capacity.fits ? 'Ya' : 'Tidak' }}</p>
      </template>
    </div>
    <h3>Riwayat</h3>
    <div class="table-scroll">
      <table>
        <caption class="text-muted">
          Riwayat putaran terbaru
        </caption>
        <thead><tr><th>Tanggal</th><th>Node</th><th>Waktu mati</th><th>Waktu menyala</th><th>Dimigrasi</th><th>Dipindah dgn restart</th><th>Dihentikan</th><th>VM dimatikan</th><th>Dipulangkan</th><th>Hasil</th><th>Pesan</th></tr></thead>
        <tbody>
          <tr
            v-for="round in status.history"
            :key="round.id"
          >
            <td>{{ round.id }}</td><td>{{ (round.nodes || []).map(n => n.name).join(', ') }}</td>
            <td>{{ nodeTimes(round, 'offAt') }}</td><td>{{ nodeTimes(round, 'onAt') }}</td>
            <td>{{ total(round, 'migrated') }}</td><td>{{ total(round, 'coldMoved') }}</td><td>{{ total(round, 'stopped') }}</td>
            <td>{{ (round.stoppedVms || []).length }}</td><td>{{ total(round, 'returned') }}</td>
            <td>{{ phase(round.result) }}</td><td>{{ round.message || '—' }}</td>
          </tr>
          <tr v-if="!(status.history || []).length">
            <td colspan="11">
              Belum ada riwayat.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <AlertSettings :request="request" />
  </section>
</template>

<style lang="scss" scoped>
.rotation-tab {
  h3 { margin: 20px 0 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 14px; }
  .label { display: block; font-weight: 600; margin-bottom: 4px; }
  .field { width: 100%; padding: 7px 10px; border: 1px solid var(--border, #ccc); border-radius: var(--border-radius, 4px); background: var(--input-bg, transparent); color: var(--input-text, inherit); }
  .checkbox { display: flex; gap: 6px; align-items: center; margin-bottom: 10px; }
  .hint { display: block; margin-top: 4px; font-size: 0.9em; }
  .badge { display: inline-block; padding: 2px 6px; border: 1px solid var(--border, #ccc); border-radius: var(--border-radius, 4px); }
  .table-scroll { overflow-x: auto; margin-top: 10px; }
  table { width: 100%; border-collapse: collapse; }
  caption { text-align: left; padding: 6px 0; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border, #ccc); overflow-wrap: anywhere; }
  .actions .btn { margin: 2px; }
  input:focus-visible, select:focus-visible, button:focus-visible { outline: 2px solid var(--primary, #0055a4); outline-offset: 1px; }
}
</style>
