'use strict';
// Do not pay a model to diagnose a missing host toolset.
const fs = require('node:fs');
exports.name = 'pharma-tool-readiness';
exports.inject = ['tools'];
exports.apply = function(ctx, config) {
  ctx.on('agent/pre-step', ({agent}, next) => {
    const missing = config.required.filter(name => !ctx.tools.get(`mcp__pharma__${name}`, agent));
    if (missing.length) {
      const marker = process.env.PHARMA_TOOL_GUARD_MARKER;
      if (marker) fs.writeFileSync(marker, JSON.stringify({code:'TASK_TOOLS_UNAVAILABLE',missing}));
      throw new Error('TASK_TOOLS_UNAVAILABLE: frozen MCP toolset is incomplete');
    }
    return next();
  }, {prepend:true});
};
