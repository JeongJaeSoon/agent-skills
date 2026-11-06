import type { Register } from 'claude-code'

import { register as briefPane } from './brief_pane'
import { register as clearBand } from './clear_band'

// A plugin loads one hooks module: this one registers each mod's hooks.
export const register: Register = (on, options) => {
  briefPane(on, options)
  clearBand(on, options)
}
