// eslint-disable-next-line import/named
// import { RouteRecordRaw } from 'vue-router';
import { PRODUCT_NAME } from '../config/harvester';

import Root from '../pages/c/_cluster/index.vue';
import HarvesterSupport from '../pages/c/_cluster/support/index.vue';
import HarvesterConsoleSerial from '../pages/c/_cluster/console/_uid/serial.vue';
import HarvesterConsoleVnc from '../pages/c/_cluster/console/_uid/vnc.vue';
import ListHarvesterResource from '../pages/c/_cluster/_resource/index.vue';
import HarvesterBrand from '../pages/c/_cluster/brand/index.vue';
import CreateHarvesterResource from '../pages/c/_cluster/_resource/create.vue';
import ViewHarvesterResource from '../pages/c/_cluster/_resource/_id.vue';
import ViewHarvesterNsResource from '../pages/c/_cluster/_resource/_namespace/_id.vue';
import HarvesterAirgapUpdgrade from '../pages/c/_cluster/airgapupgrade/index.vue';
import HarvesterMembers from '../pages/c/_cluster/members/index.vue';
import ProjectNamespaces from '../pages/c/_cluster/projectsnamespaces.vue';
import HarvesterAlertmanagerReceiver from '../pages/c/_cluster/alertmanagerconfig/_alertmanagerconfigid/receiver.vue';
import HarvesterUnsupported from '../pages/c/_cluster/unsupported/index.vue';
import HarvesterNodeShutdown from '../pages/c/_cluster/node-shutdown/index.vue';
import HarvesterVpnNetbird from '../pages/c/_cluster/vpn/netbird.vue';
import HarvesterVpnTailscale from '../pages/c/_cluster/vpn/tailscale.vue';
import HarvesterVpnZerotier from '../pages/c/_cluster/vpn/zerotier.vue';
import HarvesterVpnOpenvpn from '../pages/c/_cluster/vpn/openvpn.vue';

const routes = [
  {
    route: {
      name:      `${ PRODUCT_NAME }-c-cluster-unsupported-standalone`,
      path:      `/:product`,
      component: HarvesterUnsupported
    },
    parent: 'blank'
  },
  {
    route: {
      name:      `${ PRODUCT_NAME }-c-cluster-unsupported`,
      path:      `/:product`,
      component: HarvesterUnsupported
    },
    parent: 'plain'
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-support`,
    path:      `/:product/c/:cluster/support`,
    component: HarvesterSupport,
  },
  {
    route: {
      name:      `${ PRODUCT_NAME }-c-cluster-console-uid-serial`,
      path:      `/:product/c/:cluster/console/:uid/serial`,
      component: HarvesterConsoleSerial,
    },
    parent: 'blank'
  },
  {
    route: {
      name:      `${ PRODUCT_NAME }-c-cluster-console-uid-vnc`,
      path:      `/:product/c/:cluster/console/:uid/vnc`,
      component: HarvesterConsoleVnc,
    },
    parent: 'blank'
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-airgapupgrade`,
    path:      `/:product/c/:cluster/airgapupgrade`,
    component: HarvesterAirgapUpdgrade,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-brand`,
    path:      `/:product/c/:cluster/brand`,
    component: HarvesterBrand,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-alertmanagerconfig-alertmanagerconfigid-receiver`,
    path:      `/:product/c/:cluster/alertmanagerconfig/:alertmanagerconfigid/receiver`,
    component: HarvesterAlertmanagerReceiver,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-members`,
    path:      `/:product/c/:cluster/members`,
    component: HarvesterMembers,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-node-shutdown`,
    path:      `/:product/c/:cluster/node-shutdown`,
    component: HarvesterNodeShutdown,
  },
  // VPN add-on — one page per provider, all editing the single `vpn` add-on.
  {
    name:      `${ PRODUCT_NAME }-c-cluster-vpn-netbird`,
    path:      `/:product/c/:cluster/vpn/netbird`,
    component: HarvesterVpnNetbird,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-vpn-tailscale`,
    path:      `/:product/c/:cluster/vpn/tailscale`,
    component: HarvesterVpnTailscale,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-vpn-zerotier`,
    path:      `/:product/c/:cluster/vpn/zerotier`,
    component: HarvesterVpnZerotier,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster-vpn-openvpn`,
    path:      `/:product/c/:cluster/vpn/openvpn`,
    component: HarvesterVpnOpenvpn,
  },
  {
    name:      `${ PRODUCT_NAME }-c-cluster`,
    path:      `/:product/c/:cluster`,
    component: Root,
  }, {
    name:      `${ PRODUCT_NAME }-c-cluster-projectsnamespaces`,
    path:      `/:product/c/:cluster/projectsnamespaces`,
    component: ProjectNamespaces,
  }, {
    name:      `${ PRODUCT_NAME }-c-cluster-resource`,
    path:      `/:product/c/:cluster/:resource`,
    component: ListHarvesterResource,
  }, {
    name:      `${ PRODUCT_NAME }-c-cluster-resource-create`,
    path:      `/:product/c/:cluster/:resource/create`,
    component: CreateHarvesterResource,
  }, {
    name:      `${ PRODUCT_NAME }-c-cluster-resource-id`,
    path:      `/:product/c/:cluster/:resource/:id`,
    component: ViewHarvesterResource,
  }, {
    name:      `${ PRODUCT_NAME }-c-cluster-resource-namespace-id`,
    path:      `/:product/c/:cluster/:resource/:namespace/:id`,
    component: ViewHarvesterNsResource,
  },

];

export default routes;
