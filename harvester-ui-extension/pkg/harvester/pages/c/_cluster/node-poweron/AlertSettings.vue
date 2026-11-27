<script>
const SECRET_ID = 'harvester-system/node-shutdown-alerts';

function defaults() {
  return {
    slack:    { webhookUrl: '' },
    teams:    { webhookUrl: '' },
    email:    {
      smtpHost: '',
      smtpPort:   587,
      username:   '',
      password:   '',
      from:       '',
      to:         [],
      startTls:   true
    },
    youtrack: {
      url:      '',
      token:    '',
      project:  '',
      assignee: ''
    },
  };
}

function decodeConfig(value) {
  const bytes = Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
  const parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error();
  }
  const config = { ...parsed };

  Object.entries(defaults()).forEach(([channel, fields]) => {
    config[channel] = {
      ...fields,
      ...parsed[channel]
    };
  });
  if (!Array.isArray(config.email.to)) {
    throw new Error();
  }

  return config;
}

function encodeConfig(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));

  return btoa(Array.from(bytes, (c) => String.fromCharCode(c)).join(''));
}

export default {
  name:  'AlertSettings',
  props: {
    request: {
      type:     Function,
      required: true
    }
  },
  data() {
    return {
      secret:      null,
      config:      defaults(),
      recipients:  '',
      loaded:      false,
      saving:      false,
      testing:     '',
      error:       '',
      message:     '',
      statusError: '',
      active:      {},
      results:     {},
      channels:    [
        {
          key:    'slack',
          label:  'Slack',
          fields: [{
            key:   'webhookUrl',
            label: 'URL webhook',
            type:  'password'
          }]
        },
        {
          key:    'teams',
          label:  'Microsoft Teams',
          fields: [{
            key:   'webhookUrl',
            label: 'URL webhook Workflows',
            type:  'password'
          }]
        },
        {
          key:    'email',
          label:  'Email',
          fields: [
            {
              key:   'smtpHost',
              label: 'Host SMTP'
            },
            {
              key:   'smtpPort',
              label: 'Port SMTP',
              type:  'number'
            },
            {
              key:   'username',
              label: 'Nama pengguna SMTP'
            },
            {
              key:   'password',
              label: 'Kata sandi SMTP',
              type:  'password'
            },
            {
              key:   'from',
              label: 'Alamat pengirim'
            },
          ],
        },
        {
          key:    'youtrack',
          label:  'YouTrack',
          fields: [
            {
              key:   'url',
              label: 'URL YouTrack'
            },
            {
              key:   'token',
              label: 'Token',
              type:  'password'
            },
            {
              key:   'project',
              label: 'Nama pendek proyek (contoh: NO)'
            },
            {
              key:   'assignee',
              label: 'Login penerima tugas (opsional)'
            },
          ],
        },
      ],
    };
  },
  async mounted() {
    await Promise.all([this.load(), this.refreshStatus()]);
  },
  methods: {
    async load() {
      this.loaded = false;
      this.error = '';
      try {
        this.secret = await this.$store.dispatch('harvester/find', {
          type: 'secret',
          id:     SECRET_ID
        });
        const value = this.secret.data?.['alerts.json'];

        this.config = value ? decodeConfig(value) : defaults();
        this.recipients = this.config.email.to.join(', ');
        this.loaded = true;
      } catch (e) {
        if (Number(e?._status || e?.status || e?.response?.status) === 404) {
          this.secret = null;
          this.config = defaults();
          this.recipients = '';
          this.loaded = true;
        } else {
          this.error = 'Gagal membaca Secret notifikasi atau JSON tidak valid. Periksa akses dan isi alerts.json.';
        }
      }
    },
    async refreshStatus() {
      this.statusError = '';
      try {
        const result = await this.request('/system/alerts/status');

        this.active = result.channels || {};
      } catch (e) {
        this.statusError = 'Status kanal belum tersedia. Periksa layanan dan token akses.';
      }
    },
    async save() {
      if (!this.loaded || this.saving || this.testing) {
        return;
      }
      this.saving = true;
      this.error = '';
      this.message = '';
      this.results = {};
      try {
        const port = this.config.email.smtpPort;

        if (!Number.isInteger(port) || port < 1 || port > 65535) {
          this.error = 'Port SMTP harus bilangan bulat 1–65535.';

          return;
        }
        const data = {
          ...this.secret?.data,
          'alerts.json': encodeConfig(this.config)
        };

        if (!this.secret) {
          this.secret = await this.$store.dispatch('harvester/create', {
            type:     'secret',
            metadata: {
              namespace: 'harvester-system',
              name:        'node-shutdown-alerts'
            },
            data,
          });
        } else {
          this.secret.data = data;
        }
        await this.secret.save();
        this.message = 'Secret notifikasi disimpan. Tunggu sinkronisasi Secret ke layanan, lalu muat ulang status sebelum mengirim tes.';
        await this.refreshStatus();
      } catch (e) {
        this.error = 'Gagal menyimpan Secret notifikasi. Periksa izin akses; muat ulang jika Secret baru saja dibuat pengguna lain.';
      } finally {
        this.saving = false;
      }
    },
    setField(channel, field, value) {
      this.config[channel][field] = field === 'smtpPort' ? (value === '' ? null : Number(value)) : value;
      this.message = '';
      this.results = {};
    },
    setRecipients(value) {
      this.recipients = value;
      this.config.email.to = value.split(/[,;\n]/).map((address) => address.trim()).filter(Boolean);
      this.message = '';
    },
    async test(channel) {
      this.testing = channel;
      try {
        const result = await this.request('/system/alerts/test', 'POST', { channel });

        this.results = {
          ...this.results,
          [channel]: result.ok ? 'Pesan tes terkirim.' : 'Tes gagal. Periksa konfigurasi kanal dan koneksi layanan.'
        };
      } catch (e) {
        this.results = {
          ...this.results,
          [channel]: 'Tes gagal. Periksa layanan dan token akses.'
        };
      } finally {
        this.testing = '';
      }
    },
  },
};
</script>

<template>
  <section class="alert-settings">
    <h3>Notifikasi</h3>
    <p class="text-muted">
      Kredensial disimpan terpisah di Secret harvester-system/node-shutdown-alerts, bukan di valuesContent. Kosongkan kolom wajib untuk menonaktifkan kanal.
    </p>
    <p class="text-muted">
      Simpan notifikasi sebelum mengirim tes. Status aktif berasal dari konfigurasi yang sudah dibaca layanan.
    </p>
    <p
      v-if="error"
      class="text-error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="statusError"
      class="text-warning"
      role="status"
    >
      {{ statusError }}
    </p>
    <p
      v-if="message"
      class="text-success"
      role="status"
    >
      {{ message }}
    </p>
    <button
      type="button"
      class="btn role-secondary"
      :disabled="saving || !!testing"
      @click="refreshStatus"
    >
      Muat ulang status kanal
    </button>
    <button
      v-if="!loaded"
      type="button"
      class="btn role-secondary"
      @click="load"
    >
      Baca ulang Secret
    </button>
    <fieldset
      v-for="channel in channels"
      :key="channel.key"
      :disabled="!loaded || saving || !!testing"
    >
      <legend>{{ channel.label }} — {{ statusError || active[channel.key] === undefined ? 'Status belum tersedia' : (active[channel.key] ? 'Aktif' : 'Tidak aktif') }}</legend>
      <div class="grid">
        <label
          v-for="field in channel.fields"
          :key="field.key"
        >
          <span class="label">{{ field.label }}</span>
          <input
            :value="config[channel.key][field.key]"
            :type="field.type || 'text'"
            :min="field.type === 'number' ? 1 : undefined"
            :max="field.type === 'number' ? 65535 : undefined"
            :autocomplete="field.type === 'password' ? 'new-password' : 'off'"
            spellcheck="false"
            class="field"
            @input="setField(channel.key, field.key, $event.target.value)"
          >
        </label>
        <label v-if="channel.key === 'email'">
          <span class="label">Penerima (pisahkan dengan koma)</span>
          <input
            :value="recipients"
            type="text"
            class="field"
            @input="setRecipients($event.target.value)"
          >
        </label>
      </div>
      <label
        v-if="channel.key === 'email'"
        class="checkbox"
      ><input
        v-model="config.email.startTls"
        type="checkbox"
      > Gunakan STARTTLS (port 465 selalu memakai TLS)</label>
      <button
        type="button"
        class="btn role-secondary"
        @click="test(channel.key)"
      >
        {{ testing === channel.key ? 'Mengirim…' : 'Kirim tes' }}
      </button>
      <p
        v-if="results[channel.key]"
        role="status"
      >
        {{ results[channel.key] }}
      </p>
    </fieldset>
    <button
      type="button"
      class="btn role-primary"
      :disabled="!loaded || saving || !!testing"
      @click="save"
    >
      {{ saving ? 'Menyimpan…' : 'Simpan notifikasi' }}
    </button>
  </section>
</template>

<style lang="scss" scoped>
.alert-settings {
  h3 { margin: 20px 0 10px; }
  fieldset { border: 1px solid var(--border, #ccc); border-radius: var(--border-radius, 4px); margin: 14px 0; padding: 14px; min-width: 0; }
  legend { font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 14px; margin-bottom: 10px; }
  .label { display: block; font-weight: 600; margin-bottom: 4px; }
  .field { width: 100%; padding: 7px 10px; border: 1px solid var(--border, #ccc); border-radius: var(--border-radius, 4px); background: var(--input-bg, transparent); color: var(--input-text, inherit); }
  .checkbox { display: flex; gap: 6px; align-items: center; margin-bottom: 10px; }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--primary, #0055a4); outline-offset: 1px; }
}
</style>
