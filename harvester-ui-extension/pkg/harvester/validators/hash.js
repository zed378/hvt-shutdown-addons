export function hashSHA512(value, getters, errors, validatorArgs, displayKey) {
  if (!/^[a-f0-9]{128}$/i.test(value)) {
    errors.push(getters['i18n/t']('harvester.validation.hash.sha512'));
  }

  return errors;
}
