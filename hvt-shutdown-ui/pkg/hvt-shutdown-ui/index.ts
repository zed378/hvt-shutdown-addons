import { importTypes } from '@rancher/auto-import';
import { IPlugin, TabLocation } from '@shell/core/types';

// Init the package
export default function(plugin: IPlugin): void {
  // Auto-import model, detail, edit from the folders
  importTypes(plugin);

  // Provide plugin metadata from package.json
  plugin.metadata = require('./package.json');

  // Load a product
  // plugin.addProduct(require('./product'));

  // Register the custom Add-on tab for node-shutdown
  plugin.addTab(
    TabLocation.RESOURCE_DETAIL_PAGE,
    {
      resource: ['harvesterhci.io.addon'],
      mode: ['edit'],
      id: ['node-shutdown']
    },
    {
      name: 'node-shutdown-config',
      label: 'Node Shutdown Configuration',
      weight: 100,
      component: () => import('./components/NodeShutdownConfig.vue')
    }
  );
}
