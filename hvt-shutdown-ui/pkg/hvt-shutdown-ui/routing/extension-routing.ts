import { HARVESTER_PRODUCT, PAGE_NAME, ROUTE_NAME } from '../product';
import NodeShutdownPage from '../pages/NodeShutdown.vue';

const routes = [
  {
    name:      ROUTE_NAME,
    path:      `/${ HARVESTER_PRODUCT }/c/:cluster/${ PAGE_NAME }`,
    component: NodeShutdownPage,
    meta:      {
      product: HARVESTER_PRODUCT,
      pkg:     'hvt-shutdown-ui',
    },
  },
];

export default routes;
