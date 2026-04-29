import {
  setActiveStep as setCommonActiveStep,
  setStepState,
} from "/static-common/app/ui/steps.js?v=20260428a";

import { state } from "../core/state.js";

export { setStepState };

export function setActiveStep(stepNumber, options = {}) {
  const step = Number(stepNumber);
  if (Number.isFinite(step) && step >= 1 && step <= 3) {
    state.activeStep = step;
  }
  setCommonActiveStep(stepNumber, options);
}
