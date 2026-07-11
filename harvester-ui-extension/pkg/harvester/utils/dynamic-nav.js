/**
 * Show/hide a side-nav entry based on whether a specific Harvester Add-on is enabled.
 *
 * Adapted from Harvester's newer registerAddonSideNav, but WITHOUT the
 * schema-accessibility filter — our nav entry is a custom virtual page type
 * (e.g. 'node-shutdown'), not a real CRD, so it has no schema of its own.
 *
 * @param {Object} store        - Vuex store.
 * @param {String} productName  - Product name (e.g. 'harvester').
 * @param {Object} config
 * @param {String} config.addonName    - Add-on metadata.name to watch (e.g. 'node-shutdown').
 * @param {String} config.resourceType - Addon schema id (HCI.ADD_ONS).
 * @param {String} config.navGroup     - Side-nav group id (e.g. 'advanced').
 * @param {Array<String>} config.types - Virtual type ids to show/hide (e.g. ['node-shutdown']).
 */
export function registerAddonNav(store, productName, {
  addonName, resourceType, navGroup, types
}) {
  if (typeof window === 'undefined') {
    return;
  }

  // Nudge the SideNav to re-render (it doesn't always react to basicType changes).
  const kickSideNav = () => {
    const TRIGGER = 'ui.refresh.trigger';

    try {
      store.dispatch('type-map/addFavorite', TRIGGER);
      setTimeout(() => store.dispatch('type-map/removeFavorite', TRIGGER), 600);
    } catch (e) {
      // best-effort
    }
  };

  const showTypes = () => {
    store.commit('type-map/basicType', {
      product: productName,
      group:   navGroup,
      types
    });
  };

  const hideTypes = () => {
    const basicTypes = store.state['type-map']?.basicTypes?.[productName];

    if (basicTypes) {
      types.forEach((t) => delete basicTypes[t]);
    }
  };

  const setMenuVisibility = (visible) => {
    // Always clear first, then re-add when enabled.
    hideTypes();
    if (visible) {
      showTypes();
    }
    kickSideNav();
  };

  let attempts = 0;
  const MAX_ATTEMPTS = 60;

  const waitForStore = setInterval(() => {
    attempts++;
    try {
      const schemaFor = store.getters[`${ productName }/schemaFor`];
      const haveAll = store.getters[`${ productName }/haveAll`];
      const hasSchema = schemaFor && schemaFor(resourceType);
      const hasData = haveAll && haveAll(resourceType);

      if (hasSchema && hasData) {
        clearInterval(waitForStore);

        store.watch(
          (state, getters) => {
            const addons = getters[`${ productName }/all`](resourceType) || [];
            const addon = addons.find((a) => a.metadata?.name === addonName);

            return addon?.spec?.enabled === true;
          },
          (isEnabled) => setMenuVisibility(isEnabled),
          { immediate: true, deep: true }
        );
      } else if (hasSchema && !hasData) {
        store.dispatch(`${ productName }/findAll`, { type: resourceType });
      } else if (attempts >= MAX_ATTEMPTS) {
        clearInterval(waitForStore);
      }
    } catch (e) {
      if (attempts >= MAX_ATTEMPTS) {
        clearInterval(waitForStore);
      }
    }
  }, 1000);
}
