import { importTypes } from '@rancher/auto-import';
import { IPlugin } from '@shell/core/types';
import extensionRouting from './routing/extension-routing';

// Init the package
export default function(plugin: IPlugin): void {
  // Auto-import model, detail, edit from the folders
  importTypes(plugin);

  // Provide plugin metadata from package.json
  plugin.metadata = require('./package.json');

  // Register a top-level product (adds a nav menu item + its own page) instead of
  // an add-on tab — Harvester's add-on config page does not host injected tabs.
  plugin.addProduct(require('./product'));

  // Register the page route(s) for the product.
  plugin.addRoutes(extensionRouting);
}
