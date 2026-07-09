import { IPlugin } from '@shell/core/types';

// Instead of creating a NEW top-level product, we extend Harvester's OWN product
// so the nav item appears in the Harvester sidebar (alongside Hosts, VMs, Add-ons…).
export const HARVESTER_PRODUCT = 'harvester';
export const PAGE_NAME = 'node-shutdown';
// Harvester standalone runs as the "local" cluster.
export const CLUSTER = 'local';

export const ROUTE_NAME = `${ HARVESTER_PRODUCT }-c-cluster-${ PAGE_NAME }`;

export function init($plugin: IPlugin, store: any) {
  // Scope the DSL to the EXISTING harvester product (do not call product() — that
  // would try to define a new one).
  const {
    virtualType,
    basicType,
  } = $plugin.DSL(store, HARVESTER_PRODUCT);

  virtualType({
    labelKey:   'nodeShutdown.menuLabel',
    name:       PAGE_NAME,
    namespaced: false,
    route:      {
      name:   ROUTE_NAME,
      params: { product: HARVESTER_PRODUCT, cluster: CLUSTER },
    },
  });

  // Add it to the Harvester product's side nav.
  basicType([PAGE_NAME]);
}
