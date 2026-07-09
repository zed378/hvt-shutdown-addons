<script>
import BrandImage from '@shell/components/BrandImage';

export default {
  components: { BrandImage },

  computed: {
    serverVersion() {
      const version = this.$store.getters['harvester-common/getServerVersion']() || '';

      return version.replace('v', '');
    }
  }
};
</script>

<template>
  <div class="dashboard-root">
    <div class="dashboard-content">
      <main class="main-layout">
        <div class="logo">
          <BrandImage
            file-name="harvester.png"
            height="64"
          />
        </div>
        <div class="tagline">
          <div class="description">
            {{ t('unsupported.description') }}
          </div>
        </div>
        <div class="tagline">
          <div v-clean-html="t('unsupported.serverVersion', { serverVersion }, true)" />
        </div>
        <div class="tagline">
          <div v-clean-html="t('unsupported.learnMore', {}, true)" />
        </div>
      </main>
    </div>
  </div>
</template>

<style lang="scss" scoped>
  .dashboard-root {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }

  .dashboard-content {
    display: grid;
    flex-grow:1;

    grid-template-areas:
      "main";

    grid-template-columns: auto;
    grid-template-rows:    auto;
  }

  .logo {
    display: flex;
    justify-content: center;
    margin: 200px 0 40px 0;
  }

  .tagline {
    display: flex;
    justify-content: center;
    margin-top: 30px;

    > div {
      font-size: 16px;
      line-height: 22px;
      max-width: 80%;
      text-align: center;
    }
  }

  .description {
    font-size: 24px !important;
    font-weight: 400;
  }

</style>
